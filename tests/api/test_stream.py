"""Streaming transport: ordered replay, resume, scoped auth and the wire contract.

The trace stays the source of truth. These tests tail it through the in-process hub: a client
names the last event id it saw and receives only the rest, every subscriber sees the same order,
and a key is bound to the session it was granted for.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from ezchain_agent_runtime import (
    TRACE_SCHEMA,
    MemoryTraceLog,
    SessionState,
    StopReason,
    TraceEvent,
    TraceRecorder,
    digest_text,
)
from ezchain_api.config import Settings, get_settings
from ezchain_api.deps import get_session
from ezchain_api.routers.stream import (
    get_stream_hub,
    get_ws_hub,
    require_stream_principal,
)
from ezchain_api.routers.stream import (
    router as stream_router,
)
from ezchain_api.services import api_keys
from ezchain_api.services.stream import (
    InvalidLastEventId,
    PrincipalMismatch,
    ScopeDenied,
    StreamEvent,
    StreamHub,
    UnknownSession,
)

# Obviously-invalid constants. No real credential, funded address or network is involved.
TEST_TOKEN = "ezk_TEST_KEY_DO_NOT_USE"
FIXED_TIME = datetime(2026, 1, 1, tzinfo=UTC)


def _trace(session_id: str = "session-1", count: int = 3) -> tuple[TraceEvent, ...]:
    log = MemoryTraceLog()
    recorder = TraceRecorder(session_id=session_id, sink=log, clock=lambda: FIXED_TIME)
    recorder.session_start(prompt="first prompt")
    for index in range(max(0, count - 2)):
        recorder.turn(
            index=index,
            prompt_tokens=1,
            completion_tokens=1,
            cost_usd=Decimal("0.01"),
            tool_calls=0,
            has_answer=False,
        )
    recorder.outcome(
        state=SessionState.DONE,
        reason=StopReason.COMPLETED,
        detail=None,
        turns=count,
        tokens=count,
        cost_usd=Decimal("0.03"),
        numbers=0,
    )
    return log.read(session_id)


def _principal(
    scopes: tuple[str, ...] = ("trade",), subject: str = "agent-a"
) -> api_keys.Principal:
    return api_keys.Principal(
        subject=subject, scopes=frozenset(scopes), key_id="TEST_KEY_DO_NOT_USE"
    )


def _open(hub: StreamHub, *, scope: str = "trade", count: int = 3) -> tuple[TraceEvent, ...]:
    events = _trace(count=count)
    hub.open("session-1", owner=_principal((scope,)), required_scope=scope, events=events)
    return events


# --- Service: ordering, resume, fan-out, refusal ---------------------------------------


def test_stream_replays_in_order_and_resumes_without_duplicates() -> None:
    hub = StreamHub()
    events = _open(hub)
    ids = [str(event.sequence) for event in events]

    fresh, _ = hub.subscribe("session-1", _principal())
    resumed, _ = hub.subscribe("session-1", _principal(), last_event_id=ids[1])
    caught_up, _ = hub.subscribe("session-1", _principal(), last_event_id=str(ids[-1]))

    assert [e.id for e in fresh] == ids
    assert [e.id for e in resumed] == ids[2:]
    assert caught_up == []


def test_stream_fans_out_to_every_subscriber_in_the_same_order() -> None:
    hub = StreamHub()
    _open(hub)

    first_snapshot, first = hub.subscribe("session-1", _principal())
    second_snapshot, second = hub.subscribe("session-1", _principal())
    assert [event.id for event in first_snapshot] == ["0", "1", "2"]
    assert [event.id for event in second_snapshot] == ["0", "1", "2"]

    published = [_trace(count=count)[-1] for count in (6, 7, 8)]
    for event in published:
        hub.publish("session-1", event)

    first_ids = [first.get_nowait().id for _ in published]
    second_ids = [second.get_nowait().id for _ in published]
    assert first_ids == [str(event.sequence) for event in published]
    assert second_ids == first_ids


def test_dropped_subscriber_does_not_stop_the_stream() -> None:
    hub = StreamHub()
    _open(hub, count=0)

    _, dropped = hub.subscribe("session-1", _principal())
    _, kept = hub.subscribe("session-1", _principal())
    hub.unsubscribe("session-1", dropped)

    event = _trace(count=3)[-1]
    hub.publish("session-1", event)

    assert kept.get_nowait().id == str(event.sequence)
    assert dropped.empty()
    assert "session-1" in hub.sessions()


def test_read_key_is_refused_on_a_trade_stream_with_a_typed_error() -> None:
    hub = StreamHub()
    _open(hub, scope="trade")

    with pytest.raises(ScopeDenied) as refused:
        hub.subscribe("session-1", _principal(("read",)))

    assert refused.value.code == "STREAM_SCOPE_DENIED"
    assert refused.value.status_code == 403
    assert refused.value.detail()["retryable"] is False


def test_a_different_principal_cannot_attach_to_the_channel() -> None:
    hub = StreamHub()
    _open(hub)

    with pytest.raises(PrincipalMismatch) as refused:
        hub.subscribe("session-1", _principal(subject="someone-else"))

    assert refused.value.code == "STREAM_PRINCIPAL_MISMATCH"


def test_unknown_session_and_malformed_last_id_are_typed() -> None:
    hub = StreamHub()

    with pytest.raises(UnknownSession) as unknown:
        hub.subscribe("missing", _principal())
    assert unknown.value.code == "STREAM_SESSION_NOT_FOUND"

    _open(hub)
    for bad in ("not-an-id", "-1"):
        with pytest.raises(InvalidLastEventId) as refused:
            hub.subscribe("session-1", _principal(), last_event_id=bad)
        assert refused.value.code == "STREAM_INVALID_LAST_EVENT_ID"


def test_parse_last_id_accepts_sequences_and_refuses_garbage() -> None:
    assert StreamEvent.parse_last_id(None) is None
    assert StreamEvent.parse_last_id("") is None
    assert StreamEvent.parse_last_id("12") == 12


# --- Contract: the envelope agents parse ------------------------------------------------


def test_event_envelope_contract() -> None:
    event = StreamEvent.from_trace(_trace(count=3)[0])

    assert event.envelope() == {
        "id": "0",
        "session_id": "session-1",
        "sequence": 0,
        "schema": TRACE_SCHEMA,
        "kind": "session_start",
        "at": FIXED_TIME.astimezone(UTC).isoformat(),
        "fields": {"prompt_hash": digest_text("first prompt"), "model_id": "unknown"},
        "prev": "0" * 64,
        "hash": event.hash,
    }
    assert all(
        isinstance(event.envelope()[key], str)
        for key in ("id", "session_id", "schema", "kind", "at", "prev", "hash")
    )
    assert isinstance(event.envelope()["sequence"], int)
    assert isinstance(event.envelope()["fields"], dict)

    frame = event.sse()
    assert frame.startswith("id: 0\nevent: session_start\ndata: ")
    assert frame.endswith("\n\n")


# --- Router: SSE frames and the typed refusal path --------------------------------------


def _sse_client(
    session: Session,
    settings: Settings,
    hub: StreamHub,
    principal: api_keys.Principal | None,
) -> TestClient:
    app = FastAPI()
    app.include_router(stream_router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_stream_hub] = lambda: hub
    if principal is not None:
        app.dependency_overrides[require_stream_principal] = lambda: principal
    return TestClient(app)


def test_sse_streams_the_envelope_and_resumes(session: Session, settings: Settings) -> None:
    hub = StreamHub()
    events = _open(hub)
    hub.close("session-1")
    client = _sse_client(session, settings, hub, _principal())

    response = client.get("/api/v1/stream/session-1", params={"last_event_id": "1"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    data_line = [line for line in response.text.splitlines() if line.startswith("data: ")]
    assert len(data_line) == 1
    envelope: dict[str, Any] = json.loads(data_line[0].removeprefix("data: "))
    assert envelope == StreamEvent.from_trace(events[2]).envelope()


def test_sse_honours_the_last_event_id_header(session: Session, settings: Settings) -> None:
    hub = StreamHub()
    events = _open(hub)
    hub.close("session-1")
    client = _sse_client(session, settings, hub, _principal())

    response = client.get(
        "/api/v1/stream/session-1",
        headers={"Last-Event-ID": "1"},
    )

    data_line = [line for line in response.text.splitlines() if line.startswith("data: ")]
    assert len(data_line) == 1
    assert (
        json.loads(data_line[0].removeprefix("data: "))
        == StreamEvent.from_trace(events[2]).envelope()
    )


def test_two_sse_clients_see_the_same_order(session: Session, settings: Settings) -> None:
    hub = StreamHub()
    events = _open(hub)
    hub.close("session-1")
    client = _sse_client(session, settings, hub, _principal())

    def frames() -> list[dict[str, Any]]:
        response = client.get("/api/v1/stream/session-1")
        return [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]

    first, second = frames(), frames()
    expected = [StreamEvent.from_trace(event).envelope() for event in events]
    assert first == expected
    assert second == expected


def test_sse_refuses_a_read_key_with_a_typed_error(session: Session, settings: Settings) -> None:
    hub = StreamHub()
    _open(hub, scope="trade")
    client = _sse_client(session, settings, hub, _principal(("read",)))

    response = client.get("/api/v1/stream/session-1")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "STREAM_SCOPE_DENIED"
    assert response.json()["detail"]["retryable"] is False


def test_sse_without_a_credential_is_401(session: Session, settings: Settings) -> None:
    hub = StreamHub()
    _open(hub)
    client = _sse_client(session, settings, hub, principal=None)

    response = client.get("/api/v1/stream/session-1")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"


def test_sse_resolves_a_scoped_key_with_the_shared_helper(
    session: Session, settings: Settings
) -> None:
    session.add(
        api_keys.ApiKeyRow(
            id="test-key-id",
            subject="agent-a",
            token_hash=api_keys.hash_token(TEST_TOKEN),
            scopes=["trade"],
            created_at=FIXED_TIME,
        )
    )
    session.commit()
    hub = StreamHub()
    _open(hub)
    hub.close("session-1")
    client = _sse_client(session, settings, hub, principal=None)

    response = client.get(
        "/api/v1/stream/session-1",
        headers={"Authorization": f"Bearer {TEST_TOKEN}"},
    )

    assert response.status_code == 200


# --- WebSocket: scoped upgrade, cross-origin refusal and typed errors -------------------


def _ws_client(
    session: Session,
    settings: Settings,
    hub: StreamHub,
    principal: api_keys.Principal | None,
) -> TestClient:
    app = FastAPI()
    app.include_router(stream_router)
    from ezchain_api.routers.stream import require_ws_principal

    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_ws_hub] = lambda: hub
    if principal is not None:
        app.dependency_overrides[require_ws_principal] = lambda: principal
    return TestClient(app)


def test_websocket_streams_the_same_order(session: Session, settings: Settings) -> None:
    hub = StreamHub()
    events = _open(hub)
    client = _ws_client(session, settings, hub, _principal())

    with client.websocket_connect("/api/v1/stream/session-1") as socket:
        assert socket.receive_json() == StreamEvent.from_trace(events[0]).envelope()
        assert socket.receive_json() == StreamEvent.from_trace(events[1]).envelope()
        assert socket.receive_json() == StreamEvent.from_trace(events[2]).envelope()


def test_websocket_refuses_a_cross_origin_upgrade(session: Session, settings: Settings) -> None:
    hub = StreamHub()
    _open(hub)
    client = _ws_client(session, settings, hub, _principal())

    with pytest.raises(WebSocketDisconnect) as denied:
        with client.websocket_connect(
            "/api/v1/stream/session-1", headers={"origin": "https://elsewhere.example"}
        ):
            pass
    assert denied.value.code == 1008


def test_websocket_refuses_a_read_key_with_a_typed_error(
    session: Session, settings: Settings
) -> None:
    hub = StreamHub()
    _open(hub, scope="trade")
    client = _ws_client(session, settings, hub, _principal(("read",)))

    with client.websocket_connect("/api/v1/stream/session-1") as socket:
        assert socket.receive_json()["error"]["code"] == "STREAM_SCOPE_DENIED"
