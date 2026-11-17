"""Permission modes: one decision function, one writer, enforced on the order path."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from test_order_actor import CHAIN, DEPLOYMENT, PEPPER, TOKEN, FakeDex

from ezchain_adapters import QuoteRouter
from ezchain_api.auth import BearerAuthMiddleware
from ezchain_api.config import Settings
from ezchain_api.deps import get_dexscreener, get_quote_router, get_session
from ezchain_api.models import AgentPermissionDecisionRow, PaperFillRow
from ezchain_api.routers import agent_permissions as mode_router
from ezchain_api.routers import paper as paper_router
from ezchain_api.services import agent_permissions, api_keys

pytestmark = pytest.mark.anyio


# --- the decision function --------------------------------------------------------------


def test_the_decision_matrix() -> None:
    limit = Decimal("100")
    cases = [
        ("ask", "configure", None, False, "queue", "PERMISSION_ASK"),
        ("ask", "trade", None, False, "queue", "PERMISSION_ASK"),
        ("auto", "configure", None, False, "allow", "PERMISSION_AUTO"),
        ("auto", "trade", Decimal("50"), False, "allow", "PERMISSION_AUTO"),
        ("auto", "trade", Decimal("100"), False, "allow", "PERMISSION_AUTO"),
        ("auto", "trade", Decimal("101"), False, "queue", "PERMISSION_LIMIT_EXCEEDED"),
        ("read_only", "read", None, False, "allow", "PERMISSION_READ"),
        ("read_only", "configure", None, False, "refuse", "PERMISSION_READ_ONLY"),
        ("read_only", "trade", None, False, "refuse", "PERMISSION_READ_ONLY"),
        ("auto", "trade", Decimal("1"), True, "refuse", "KILL_SWITCH_ACTIVE"),
    ]
    for mode, effect, amount, switch, outcome, code in cases:
        assert agent_permissions.decide(
            mode=mode,  # type: ignore[arg-type]
            effect=effect,  # type: ignore[arg-type]
            limit_usd=limit,
            amount_usd=amount,
            kill_switch_active=switch,
        ) == (outcome, code)


def test_read_only_queues_a_reduce_only_exit() -> None:
    # A refusal here would strand the position, so a person still sees the exit.
    assert agent_permissions.decide(
        mode="read_only",
        effect="trade",
        limit_usd=Decimal("100"),
        amount_usd=Decimal("10"),
        exit_only=True,
    ) == ("queue", "PERMISSION_READ_ONLY")


def test_kill_switch_does_not_refuse_a_reduce_only_exit() -> None:
    # The switch stops the agent; an exit must reach a person in every mode.
    for mode in ("ask", "auto", "read_only"):
        assert agent_permissions.decide(
            mode=mode,  # type: ignore[arg-type]
            effect="trade",
            limit_usd=Decimal("100"),
            amount_usd=Decimal("10"),
            kill_switch_active=True,
            exit_only=True,
        ) == ("queue", "KILL_SWITCH_ACTIVE")


def test_read_only_still_refuses_an_entry() -> None:
    assert agent_permissions.decide(
        mode="read_only",
        effect="trade",
        limit_usd=Decimal("100"),
        amount_usd=Decimal("10"),
    ) == ("refuse", "PERMISSION_READ_ONLY")


def test_unknown_mode_fails_closed_to_ask() -> None:
    assert agent_permissions.normalize_mode("legacy") == "ask"
    assert agent_permissions.decide(mode="legacy", effect="trade", limit_usd=Decimal("1")) == (
        "queue",
        "PERMISSION_ASK",
    )


def test_the_mode_is_a_machine_with_one_writer() -> None:
    assert agent_permissions.MODES == ("ask", "auto", "read_only")
    for state, action in agent_permissions.STATES.items():
        assert action
        assert agent_permissions.ACTIONS[state]
    assert agent_permissions.TRANSITIONS["read_only"] == ("ask",)


# --- persistence and API ----------------------------------------------------------------


@pytest.fixture
def authed_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"api_token": SecretStr(DEPLOYMENT), "api_key_pepper": SecretStr(PEPPER)}
    )


@pytest.fixture
def key_factory(engine: Any) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def _client(settings: Settings, session: Session, key_factory: sessionmaker[Session]) -> TestClient:
    source = FakeDex()
    app = FastAPI()
    app.add_middleware(BearerAuthMiddleware, settings=settings, session_factory=key_factory)
    app.include_router(paper_router.router)
    app.include_router(mode_router.router)
    app.dependency_overrides[get_session] = lambda: key_factory()
    app.dependency_overrides[get_dexscreener] = lambda: source
    app.dependency_overrides[get_quote_router] = lambda: QuoteRouter(
        providers=[],
        dexscreener=source,  # type: ignore[arg-type]
    )
    return TestClient(app)


def _agent_token(key_factory: sessionmaker[Session], subject: str = "hermes") -> str:
    with key_factory() as session:
        _, token = api_keys.create_key(
            session, subject=subject, scopes=["read", "trade"], expires_in_days=30, pepper=PEPPER
        )
        return token


def _bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def test_mode_defaults_to_ask_and_only_a_person_changes_it(
    engine: Any, authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    client = _client(authed_settings, session, key_factory)
    token = _agent_token(key_factory)

    read = client.get("/api/v1/agent/permission-mode", headers=_bearer(token))
    assert read.status_code == 200
    assert read.json()["mode"] == "ask"
    assert read.json()["max_order_usd"] == str(authed_settings.max_order_usd)

    denied = client.put(
        "/api/v1/agent/permission-mode",
        json={"mode": "auto"},
        headers=_bearer(token),
    )
    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] == "PERMISSION_REQUIRES_PERSON"

    changed = client.put(
        "/api/v1/agent/permission-mode",
        json={"mode": "auto", "user_id": "hermes"},
        headers=_bearer(DEPLOYMENT),
    )
    assert changed.status_code == 200
    assert changed.json()["mode"] == "auto"
    assert (
        client.get("/api/v1/agent/permission-mode", headers=_bearer(token)).json()["mode"] == "auto"
    )


def test_an_agent_cannot_read_another_owners_mode(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    client = _client(authed_settings, session, key_factory)
    token = _agent_token(key_factory)
    response = client.get(
        "/api/v1/agent/permission-mode", params={"user_id": "someone-else"}, headers=_bearer(token)
    )
    assert response.status_code == 200
    assert response.json()["user_id"] == "hermes"


# --- the order gate (money path) --------------------------------------------------------


def _place(client: TestClient, token: str, key: str, amount: str = "25") -> Any:
    return client.post(
        "/api/v1/paper/orders",
        json={
            "chain": CHAIN,
            "token_address": TOKEN,
            "side": "buy",
            "amount_usd": amount,
            "idempotency_key": key,
        },
        headers=_bearer(token),
    )


def test_ask_queues_and_read_only_refuses_an_agent_order(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    client = _client(authed_settings, session, key_factory)
    token = _agent_token(key_factory)

    asked = _place(client, token, "perm-ask-1")
    assert asked.status_code == 201
    assert asked.json()["status"] == "pending_approval"

    client.put(
        "/api/v1/agent/permission-mode",
        json={"mode": "read_only", "user_id": "hermes"},
        headers=_bearer(DEPLOYMENT),
    )
    refused = _place(client, token, "perm-readonly-1")
    # The platform reports an order-level refusal as a rejected order, never a fill.
    assert refused.status_code == 201
    assert refused.json()["status"] == "rejected"
    assert refused.json()["reason"] == "PERMISSION_READ_ONLY"
    assert session.scalars(select(PaperFillRow)).all() == []


def _open_a_position(client: TestClient) -> None:
    opened = client.post(
        "/api/v1/paper/orders",
        json={
            "chain": CHAIN,
            "token_address": TOKEN,
            "side": "buy",
            "amount_usd": "25",
            "idempotency_key": "perm-exit-open-1",
        },
        headers=_bearer(DEPLOYMENT),
    )
    assert opened.json()["status"] == "filled"


def _agent_exit(client: TestClient, token: str, key: str) -> Any:
    return client.post(
        "/api/v1/paper/orders",
        json={
            "chain": CHAIN,
            "token_address": TOKEN,
            "side": "sell",
            "amount_usd": "10",
            "reduce_only": True,
            "idempotency_key": key,
        },
        headers=_bearer(token),
    )


def test_read_only_queues_an_agent_exit_on_the_order_path(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    client = _client(authed_settings, session, key_factory)
    _open_a_position(client)
    client.put(
        "/api/v1/agent/permission-mode",
        json={"mode": "read_only", "user_id": "hermes"},
        headers=_bearer(DEPLOYMENT),
    )

    exit_order = _agent_exit(client, _agent_token(key_factory), "perm-exit-readonly-1")
    assert exit_order.status_code == 201
    assert exit_order.json()["status"] == "pending_approval"


def test_every_gate_decision_is_appended(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    client = _client(authed_settings, session, key_factory)
    token = _agent_token(key_factory)
    _place(client, token, "perm-log-1")

    with key_factory() as check:
        rows = check.scalars(select(AgentPermissionDecisionRow)).all()
        assert [row.code for row in rows] == ["PERMISSION_ASK"]
        assert rows[0].user_id == "hermes"
        assert rows[0].outcome == "queue"

    logged = client.get(
        "/api/v1/agent/permission-decisions",
        params={"user_id": "hermes"},
        headers=_bearer(DEPLOYMENT),
    )
    assert logged.status_code == 200
    assert logged.json()["decisions"][0]["code"] == "PERMISSION_ASK"


def test_kill_switch_overrides_auto_mode(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    from ezchain_api.models import SystemFlagRow

    client = _client(authed_settings, session, key_factory)
    token = _agent_token(key_factory)
    client.put(
        "/api/v1/agent/permission-mode",
        json={"mode": "auto", "user_id": "hermes"},
        headers=_bearer(DEPLOYMENT),
    )
    session.add(SystemFlagRow(key="engine_kill_switch", value={"active": True}))
    session.commit()

    refused = _place(client, token, "perm-switch-1")
    assert refused.status_code == 201
    assert refused.json()["status"] == "rejected"
    # The platform's own kill-switch gate fires first; either code means no order moved.
    assert refused.json()["reason"] in {"KILL_SWITCH_ACTIVE", "KILL_SWITCH_ARMED"}
