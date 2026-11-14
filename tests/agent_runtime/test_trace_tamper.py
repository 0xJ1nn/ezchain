"""What a trace proves: keyed it is evidence, unkeyed it is only a checksum.

A reviewer reproduced two forgeries against the previous chain, which had no key and no
external anchor: keep ``session_start`` and the kill-switch observation, append a fabricated
``outcome`` whose ``prev`` is the last real digest, and verification passes while replay returns
the forged verdict; a full rewrite from GENESIS verifies clean too. Both are attacks by someone
who can write the trace file, and both are what this file pins.

The key lives outside the trace directory (ADR-0042's audit trail, amended). With it, an event's
digest is an HMAC that a forger cannot recompute, so the two attacks are refused. Without it the
trace is explicitly chain-only: the attacks still work, and that is asserted too, because a
docstring claiming tamper evidence without a key would be the bug.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from agent_helpers import ACCOUNT, ACCOUNT_RESULT, FakeMcp, FakeModel, FixedClock, make_limits

from ezchain_agent_runtime import (
    AgentLoop,
    Answer,
    ModelTurn,
    SessionState,
    StopReason,
    ToolCall,
    TraceChainBroken,
    TraceEvent,
    TraceEventKind,
    TraceIncomplete,
    anchor_of,
    replay,
    verify_events,
)
from ezchain_agent_runtime.trace import FileTraceLog, MemoryTraceLog, resolve_key
from ezchain_agent_runtime.trace.key import MIN_KEY_BYTES, TRACE_KEY_ENV, is_keyed

KEY = b"k" * MIN_KEY_BYTES
OTHER_KEY = b"j" * MIN_KEY_BYTES
AT = datetime(2026, 11, 14, 9, 0, 30, tzinfo=UTC)


def _record(root: Path, *, key: bytes | None = None) -> tuple[FileTraceLog, str]:
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
        trace=FileTraceLog(root, key=key),
    )
    outcome = loop.run("what is my equity?")
    return FileTraceLog(root, key=key), outcome.session_id


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write(path: Path, events: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(event, sort_keys=True) + "\n" for event in events))


def _forge_tail(path: Path, *, keyed: bool) -> None:
    """The reproduced attack: keep the head, fabricate a COMPLETED outcome on top of it.

    A forger who has read the file knows the digest format, so when the trace is keyed the
    fabricated digest still wears the keyed prefix — it is simply a value the forger cannot
    compute the HMAC for. That is the attack, not a downgrade to an unkeyed line.
    """
    events = _lines(path)
    kept = [event for event in events if event["kind"] != "outcome"]
    kept = [event for event in kept if event["kind"] != "budget_debit"][:2]
    last = kept[-1]
    fabricated = TraceEvent.create(
        session_id=last["session_id"],
        sequence=len(kept),
        kind=TraceEventKind.OUTCOME,
        at=AT,
        fields={
            "state": "done",
            "reason": "COMPLETED",
            "detail": None,
            "turns": 0,
            "tokens": 0,
            "cost_usd": "0",
            "numbers": 0,
            "approval_id": None,
        },
        previous_hash=last["hash"],
        key=None,
    )
    line = json.loads(fabricated.line())
    if keyed:
        line["hash"] = "hmac-sha256:" + "0" * 64
    _write(path, [*kept, line])


def _rewrite_from_genesis(path: Path, *, keyed: bool) -> None:
    """The second reproduced attack: replace the whole file with an invented history."""
    session_id = _lines(path)[0]["session_id"]
    events = []
    head = "0" * 64
    for sequence, kind, fields in (
        (
            0,
            TraceEventKind.SESSION_START,
            {"prompt_hash": "invented", "model_id": "some-model"},
        ),
        (
            1,
            TraceEventKind.OUTCOME,
            {
                "state": "done",
                "reason": "COMPLETED",
                "detail": "a session that never happened",
                "turns": 1,
                "tokens": 10,
                "cost_usd": "0.01",
                "numbers": 0,
                "approval_id": None,
            },
        ),
    ):
        event = TraceEvent.create(
            session_id=session_id,
            sequence=sequence,
            kind=kind,
            at=AT,
            fields=fields,
            previous_hash=head,
            key=None,
        )
        forged = json.loads(event.line())
        if keyed:
            forged["hash"] = "hmac-sha256:" + "1" * 64
        events.append(forged)
        head = forged["hash"]
    _write(path, events)


def test_a_keyed_trace_is_sealed_with_an_hmac_not_a_bare_hash(tmp_path: Path) -> None:
    store, session_id = _record(tmp_path, key=KEY)

    events = store.read(session_id)
    assert all(is_keyed(event.digest) for event in events)
    assert store.key == KEY
    verify_events(events, key=KEY)


def test_truncate_and_reforge_is_rejected_on_a_keyed_trace(tmp_path: Path) -> None:
    store, session_id = _record(tmp_path, key=KEY)
    path = tmp_path / f"{session_id}.jsonl"
    real = store.read(session_id)
    assert real[-1].kind is TraceEventKind.OUTCOME

    _forge_tail(path, keyed=True)
    forged = FileTraceLog(tmp_path, key=KEY).read(session_id)
    assert forged[-1].fields["reason"] == "COMPLETED"
    with pytest.raises(TraceChainBroken) as caught:
        verify_events(forged, key=KEY)
    assert caught.value.reason == "digest"
    with pytest.raises(TraceChainBroken):
        replay(forged, key=KEY)


def test_a_full_rewrite_from_genesis_is_rejected_on_a_keyed_trace(tmp_path: Path) -> None:
    store, session_id = _record(tmp_path, key=KEY)
    path = tmp_path / f"{session_id}.jsonl"

    _rewrite_from_genesis(path, keyed=True)

    rewritten = FileTraceLog(tmp_path, key=KEY).read(session_id)
    with pytest.raises(TraceChainBroken) as caught:
        replay(rewritten, key=KEY)
    assert caught.value.reason == "digest"


def test_a_keyed_trace_is_not_readable_without_its_key(tmp_path: Path) -> None:
    store, session_id = _record(tmp_path, key=KEY)
    events = store.read(session_id)

    with pytest.raises(TraceChainBroken) as caught:
        verify_events(events)
    assert caught.value.reason == "key"


def test_an_unkeyed_rewrite_cannot_posit_as_an_unkeyed_trace_when_a_key_is_required(
    tmp_path: Path,
) -> None:
    """Stripping the key off every event must not turn a keyed trace into a passing one."""
    store, session_id = _record(tmp_path, key=KEY)
    unkeyed = tuple(
        TraceEvent(
            session_id=event.session_id,
            sequence=event.sequence,
            kind=event.kind,
            at=event.at,
            fields=event.fields,
            previous_hash=event.previous_hash,
            digest=event.digest.removeprefix("hmac-sha256:"),
        )
        for event in store.read(session_id)
    )

    with pytest.raises(TraceChainBroken) as caught:
        verify_events(unkeyed, key=KEY)
    assert caught.value.reason == "key"


def test_the_same_two_forgeries_still_pass_an_unkeyed_trace(tmp_path: Path) -> None:
    """The documented weakness, asserted: chain-only means exactly this and nothing more."""
    store, session_id = _record(tmp_path)
    path = tmp_path / f"{session_id}.jsonl"
    assert not any(is_keyed(event.digest) for event in store.read(session_id))

    _forge_tail(path, keyed=False)
    forged = FileTraceLog(tmp_path).read(session_id)
    assert replay(forged).reason is StopReason.COMPLETED
    assert replay(forged).session_id == session_id

    other = tmp_path / "unkeyed-rewrite"
    other.mkdir()
    other_store, other_id = _record(other)
    _rewrite_from_genesis(other / f"{other_id}.jsonl", keyed=False)
    rewritten = FileTraceLog(other).read(other_id)
    assert replay(rewritten).detail == "a session that never happened"
    assert other_store.key is None


def test_an_anchor_catches_a_truncation_the_chain_cannot_see(tmp_path: Path) -> None:
    store, session_id = _record(tmp_path, key=KEY)
    events = store.read(session_id)
    anchor = anchor_of(events)

    verify_events(events, key=KEY, anchor=anchor)
    assert replay(events, key=KEY, anchor=anchor).state is SessionState.DONE

    with pytest.raises(TraceChainBroken) as caught:
        verify_events(events[:-1], key=KEY, anchor=anchor)
    assert caught.value.reason == "truncated"

    extended = (*events, events[-1])
    with pytest.raises(TraceChainBroken) as caught:
        verify_events(extended, key=KEY, anchor=anchor)
    assert caught.value.reason in {"sequence", "anchor"}


def test_an_anchor_of_another_session_is_refused(tmp_path: Path) -> None:
    store, session_id = _record(tmp_path, key=KEY)
    events = store.read(session_id)
    elsewhere = anchor_of(events).__class__(
        session_id="someone-else", count=len(events), head=events[-1].digest
    )

    with pytest.raises(TraceChainBroken) as caught:
        verify_events(events, key=KEY, anchor=elsewhere)
    assert caught.value.reason == "anchor"


def test_a_key_stored_inside_the_trace_directory_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "traces"
    root.mkdir()
    (root / "trace.key").write_bytes(KEY)

    with pytest.raises(ValueError, match="inside the trace directory"):
        FileTraceLog(root, key_path=root / "trace.key")


def test_a_key_file_outside_the_trace_directory_is_used(tmp_path: Path) -> None:
    key_file = tmp_path / "trace.key"
    key_file.write_bytes(KEY + b"\n")
    root = tmp_path / "traces"

    store = FileTraceLog(root, key_path=key_file)

    assert store.key == KEY
    assert resolve_key(root=root, key_path=key_file) == KEY


def test_a_key_too_short_to_be_one_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least"):
        FileTraceLog(tmp_path, key=b"short")


def test_the_environment_key_is_used_and_an_empty_one_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TRACE_KEY_ENV, KEY.decode())
    assert FileTraceLog(tmp_path).key == KEY

    monkeypatch.setenv(TRACE_KEY_ENV, "")
    with pytest.raises(ValueError, match="at least"):
        FileTraceLog(tmp_path)


def test_a_store_refuses_an_event_sealed_with_another_key(tmp_path: Path) -> None:
    keyed = FileTraceLog(tmp_path, key=KEY)
    unkeyed_event = TraceEvent.create(
        session_id="s1",
        sequence=0,
        kind=TraceEventKind.SESSION_START,
        at=AT,
        fields={"prompt_hash": "x"},
        previous_hash="0" * 64,
        key=None,
    )

    with pytest.raises(Exception, match="not keyed"):
        keyed.append(unkeyed_event)

    foreign = TraceEvent.create(
        session_id="s1",
        sequence=0,
        kind=TraceEventKind.SESSION_START,
        at=AT,
        fields={"prompt_hash": "x"},
        previous_hash="0" * 64,
        key=OTHER_KEY,
    )
    keyed.append(foreign)
    with pytest.raises(TraceChainBroken):
        verify_events(keyed.read("s1"), key=KEY)


def test_the_memory_store_uses_the_key_it_is_given() -> None:
    store = MemoryTraceLog(key=KEY)

    assert store.key == KEY
    store.append(
        TraceEvent.create(
            session_id="s1",
            sequence=0,
            kind=TraceEventKind.SESSION_START,
            at=AT,
            fields={"prompt_hash": "x"},
            previous_hash="0" * 64,
            key=KEY,
        )
    )
    verify_events(store.read("s1"), key=KEY)
    with pytest.raises(TraceChainBroken):
        verify_events(store.read("s1"))


def test_a_replayed_prefix_is_incomplete_even_though_it_verifies(tmp_path: Path) -> None:
    """``verify_events`` checks integrity; completeness is ``replay``'s job and it says so."""
    store, session_id = _record(tmp_path, key=KEY)
    events = store.read(session_id)
    prefix = events[:4]

    verify_events(prefix, key=KEY)
    with pytest.raises(TraceIncomplete):
        replay(prefix, key=KEY)
