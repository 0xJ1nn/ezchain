"""Tamper evidence: a hash chain and its four mutations.

The ledger seal review found the earlier chain joined its fields with ``"|"``, so
two different tapes could hash the same. This chain hashes one canonical JSON object per
event instead, and :func:`test_a_delimiter_join_would_collide_where_json_does_not` proves the
difference rather than asserting it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from agent_helpers import ACCOUNT, ACCOUNT_RESULT, FakeMcp, FakeModel, FixedClock, make_limits

from ezchain_agent_runtime import AgentLoop, Answer, ModelTurn, ToolCall
from ezchain_agent_runtime.trace import (
    GENESIS,
    FileTraceLog,
    TraceChainBroken,
    TraceEncodingError,
    TraceEvent,
    TraceEventKind,
    TraceStoreError,
    canonical,
    digest_json,
    verify_events,
)

AT = datetime(2026, 11, 14, 9, 0, 30, tzinfo=UTC)
TOOL_EVENT = 5


def _event(**fields: Any) -> TraceEvent:
    return TraceEvent.create(
        session_id="s1",
        sequence=1,
        kind=TraceEventKind.TOOL_CALL,
        at=AT,
        fields=fields,
        previous_hash=GENESIS,
    )


def test_a_delimiter_join_would_collide_where_json_does_not() -> None:
    left = _event(tool="a|b", arguments_digest="c", status="ok")
    right = _event(tool="a", arguments_digest="b|c", status="ok")

    naive_left = "|".join(("s1", "1", "tool_call", "a|b", "c", "ok"))
    naive_right = "|".join(("s1", "1", "tool_call", "a", "b|c", "ok"))
    assert naive_left == naive_right

    assert canonical(left.record()) != canonical(right.record())
    assert left.digest != right.digest


def test_two_events_differing_only_in_a_json_type_do_not_collide() -> None:
    numeric = _event(tool="x", arguments_digest="d", status="ok", tokens=1)
    textual = _event(tool="x", arguments_digest="d", status="ok", tokens="1")

    assert canonical(numeric.record()) != canonical(textual.record())
    assert numeric.digest != textual.digest


def test_a_float_is_refused_because_it_has_no_canonical_form() -> None:
    with pytest.raises(TraceEncodingError):
        _event(tool="x", arguments_digest="d", status="ok", cost=0.1)


def test_tool_data_with_a_delimiter_cannot_share_a_digest() -> None:
    left = {"symbol": "ETH|USDC", "amount": "250"}
    right = {"symbol": "ETH", "amount": "USDC|250"}

    assert digest_json(left) != digest_json(right)


def test_tool_data_keeps_a_number_and_its_text_apart() -> None:
    assert digest_json({"amount": 1}) != digest_json({"amount": "1"})
    assert digest_json({"amount": True}) != digest_json({"amount": "true"})


def test_tool_data_that_is_not_json_native_is_refused_rather_than_stringified() -> None:
    with pytest.raises(TraceEncodingError):
        digest_json({"amount": Decimal("1")})
    with pytest.raises(TraceEncodingError):
        digest_json({1: "amount"})


def _record(tmp_path: Path) -> tuple[FileTraceLog, str]:
    model = FakeModel(
        [
            ModelTurn(tool_calls=(ToolCall(id="c1", name=ACCOUNT.name, arguments={}),)),
            ModelTurn(answer=Answer(summary="Read from the account.")),
        ]
    )
    loop = AgentLoop(
        model=model,
        toolbox=FakeMcp(results={ACCOUNT.name: ACCOUNT_RESULT}),
        limits=make_limits(),
        clock=FixedClock(),
        trace=FileTraceLog(tmp_path),
    )
    outcome = loop.run("what is my equity?")
    return FileTraceLog(tmp_path), outcome.session_id


def _lines(path: Path) -> list[str]:
    return path.read_text().splitlines()


def _rewrite(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n")


def _encode(event: dict[str, Any]) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"))


def _tampered(tmp_path: Path, mutate: Any) -> TraceChainBroken:
    store, session_id = _record(tmp_path)
    path = tmp_path / f"{session_id}.jsonl"
    _rewrite(path, mutate(_lines(path)))
    with pytest.raises(TraceChainBroken) as caught:
        verify_events(store.read(session_id))
    return caught.value


def test_a_mutated_event_is_detected(tmp_path: Path) -> None:
    def mutate(lines: list[str]) -> list[str]:
        event = json.loads(lines[TOOL_EVENT])
        event["fields"]["tool"] = "ezchain_place_order"
        lines[TOOL_EVENT] = _encode(event)
        return lines

    broken = _tampered(tmp_path, mutate)

    assert broken.index == TOOL_EVENT
    assert broken.reason == "digest"


def test_a_reordered_event_is_detected(tmp_path: Path) -> None:
    def mutate(lines: list[str]) -> list[str]:
        lines[TOOL_EVENT], lines[TOOL_EVENT + 1] = lines[TOOL_EVENT + 1], lines[TOOL_EVENT]
        return lines

    broken = _tampered(tmp_path, mutate)

    assert broken.index == TOOL_EVENT


def test_a_dropped_event_is_detected(tmp_path: Path) -> None:
    def mutate(lines: list[str]) -> list[str]:
        del lines[3]
        return lines

    broken = _tampered(tmp_path, mutate)

    assert broken.index == 3


def test_a_duplicated_event_is_detected(tmp_path: Path) -> None:
    def mutate(lines: list[str]) -> list[str]:
        lines.insert(4, lines[3])
        return lines

    broken = _tampered(tmp_path, mutate)

    assert broken.index == 4


def test_renumbering_the_events_after_a_drop_does_not_hide_it(tmp_path: Path) -> None:
    def mutate(lines: list[str]) -> list[str]:
        del lines[3]
        for index, line in enumerate(lines[3:], start=3):
            event = json.loads(line)
            event["seq"] = index
            lines[index] = _encode(event)
        return lines

    broken = _tampered(tmp_path, mutate)

    assert broken.reason == "digest"


def test_an_event_with_a_forged_link_is_detected() -> None:
    first = TraceEvent.create(
        session_id="s1",
        sequence=0,
        kind=TraceEventKind.SESSION_START,
        at=AT,
        fields={"prompt_hash": "x"},
        previous_hash=GENESIS,
    )
    second = TraceEvent.create(
        session_id="s1",
        sequence=1,
        kind=TraceEventKind.OUTCOME,
        at=AT,
        fields={"state": "done"},
        previous_hash="f" * 64,
    )

    with pytest.raises(TraceChainBroken) as caught:
        verify_events([first, second])

    assert caught.value.reason == "previous_hash"


def test_verification_names_the_event_and_the_reason(tmp_path: Path) -> None:
    store, session_id = _record(tmp_path)
    path = tmp_path / f"{session_id}.jsonl"
    lines = _lines(path)
    lines[2] = lines[2].replace('"model_id":"unknown"', '"model_id":"other"')
    _rewrite(path, lines)

    with pytest.raises(TraceChainBroken) as caught:
        verify_events(store.read(session_id))

    assert "event 2" in str(caught.value)
    assert caught.value.reason == "digest"


def test_a_line_with_an_extra_field_is_refused_by_the_store(tmp_path: Path) -> None:
    store, session_id = _record(tmp_path)
    path = tmp_path / f"{session_id}.jsonl"
    lines = _lines(path)
    event = json.loads(lines[2])
    event["note"] = "added by someone else"
    lines[2] = _encode(event)
    _rewrite(path, lines)

    with pytest.raises(TraceStoreError):
        store.read(session_id)
