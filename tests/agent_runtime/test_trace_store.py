"""The store's two failure modes a chain alone cannot cover: concurrency and shared state.

Two writers must not both extend one file — the previous version read the last line outside any
lock, so two concurrent appends passed the check and wrote seq ``[0, 1, 1]``, leaving a file no
one can verify. And a reader must not be able to mutate the log: ``MemoryTraceLog.read`` used to
hand back the stored events, whose ``fields`` dict every caller shared.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from agent_helpers import FixedClock

from ezchain_agent_runtime.trace import (
    GENESIS,
    FileTraceLog,
    MemoryTraceLog,
    TraceEvent,
    TraceEventKind,
    TraceRecorder,
    TraceStoreError,
    TraceWriteFailed,
    verify_events,
)

AT = datetime(2026, 11, 14, 9, 0, 30, tzinfo=UTC)


def _start(session_id: str, *, key: bytes | None = None) -> TraceEvent:
    return TraceEvent.create(
        session_id=session_id,
        sequence=0,
        kind=TraceEventKind.SESSION_START,
        at=AT,
        fields={"prompt_hash": "x", "model_id": "model-x"},
        previous_hash=GENESIS,
        key=key,
    )


def _next(session_id: str, last: TraceEvent) -> TraceEvent:
    return TraceEvent.create(
        session_id=session_id,
        sequence=last.sequence + 1,
        kind=TraceEventKind.KILL_SWITCH,
        at=AT,
        fields={"armed": False, "reason": ""},
        previous_hash=last.digest,
    )


def _run_together(actions: list[Any], *, timeout: float = 0.25) -> list[Any]:
    """Run each action in its own thread and collect what it raised, if anything.

    The barrier has a timeout on purpose: a correct store serialises the two writers on the
    file lock, so the second one never reaches the barrier and must time out instead of hanging.
    """
    barrier = threading.Barrier(len(actions))
    failures: list[BaseException] = []

    def run(action: Any) -> None:
        try:
            action(barrier)
        except BaseException as exc:  # noqa: BLE001 - collected, then asserted on
            failures.append(exc)

    threads = [threading.Thread(target=run, args=(action,)) for action in actions]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads), "a writer hung"
    return failures


def test_two_writers_cannot_both_extend_one_session(tmp_path: Path) -> None:
    """The reproduced race: two appends that both read seq 0 must not both write seq 1."""
    store = FileTraceLog(tmp_path)
    store.append(_start("s1"))
    first = store.read("s1")[0]

    original = FileTraceLog._last_from
    seen: dict[str, threading.Barrier] = {}

    def slow_last_from(self: FileTraceLog, fd: int, session_id: str) -> Any:
        last = original(self, fd, session_id)
        try:
            seen["barrier"].wait(timeout=0.25)  # only one writer holds the lock, so this times out
        except threading.BrokenBarrierError:
            pass
        return last

    def writer(barrier: threading.Barrier) -> None:
        seen["barrier"] = barrier
        FileTraceLog(tmp_path).append(_next("s1", first))

    FileTraceLog._last_from = slow_last_from  # type: ignore[method-assign]
    try:
        failures = _run_together([writer, writer])
    finally:
        FileTraceLog._last_from = original  # type: ignore[method-assign]

    assert len(failures) == 1
    assert isinstance(failures[0], TraceStoreError)
    events = store.read("s1")
    assert [event.sequence for event in events] == [0, 1]
    verify_events(events)


def test_two_processes_cannot_both_create_one_session(tmp_path: Path) -> None:
    """``O_EXCL`` creation: exactly one first event lands, whatever the interleaving."""
    failures = _run_together(
        [
            lambda barrier: FileTraceLog(tmp_path).append(_start("s1")),
            lambda barrier: FileTraceLog(tmp_path).append(_start("s1")),
            lambda barrier: FileTraceLog(tmp_path).append(_start("s1")),
        ]
    )

    assert len(failures) == 2
    events = FileTraceLog(tmp_path).read("s1")
    assert [event.sequence for event in events] == [0]
    verify_events(events)


def test_a_second_writer_cannot_continue_a_session_it_did_not_record(tmp_path: Path) -> None:
    """The docstring's claim, tested: no silent continuation after someone else's events."""
    first = TraceRecorder(session_id="s1", sink=FileTraceLog(tmp_path), clock=FixedClock())
    first.session_start(prompt="go", model_id="model-x")

    with pytest.raises(TraceWriteFailed):
        TraceRecorder(
            session_id="s1", sink=FileTraceLog(tmp_path), clock=FixedClock()
        ).session_start(prompt="again", model_id="model-x")


def test_a_memory_read_is_a_copy_not_the_stored_log() -> None:
    store = MemoryTraceLog()
    recorder = TraceRecorder(session_id="s1", sink=store, clock=FixedClock())
    recorder.session_start(prompt="go", model_id="model-x")
    recorder.kill_switch(armed=False, reason="clear")

    handed_out = store.read("s1")
    handed_out[0].fields["prompt_hash"] = "rewritten by the caller"  # type: ignore[index]
    handed_out[0].fields["model_id"] = "also rewritten"  # type: ignore[index]

    fresh = store.read("s1")
    assert fresh[0].fields["prompt_hash"] != "rewritten by the caller"
    assert fresh[0].fields["model_id"] == "model-x"
    assert len(fresh) == 2


def test_a_file_read_does_not_alias_another_reads_fields(tmp_path: Path) -> None:
    """Two reads are independent: editing one cannot change the other's view."""
    store = FileTraceLog(tmp_path)
    store.append(_start("s1"))

    first = store.read("s1")
    second = store.read("s1")

    assert first[0].fields == second[0].fields
    assert first[0].fields is not second[0].fields


def test_json_written_by_a_third_party_is_still_validated(tmp_path: Path) -> None:
    """A line that is not an event is refused at read time, not at verification time."""
    store = FileTraceLog(tmp_path)
    store.append(_start("s1"))
    path = tmp_path / "s1.jsonl"
    path.write_text(path.read_text() + json.dumps({"schema": "other"}) + "\n")

    with pytest.raises(TraceStoreError):
        store.read("s1")
