"""The trace has one operation: append.

There is no update, no delete and no rewrite on the store, and the file store opens a trace
with exactly one mode: ``"a"``. Reading goes through ``read_text``, which cannot truncate. The
proof is structural on purpose — a mutable log with a delete would still pass a happy-path
test while losing the property ADR-0042 asks for.
"""

from __future__ import annotations

import ast
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agent_helpers import ACCOUNT, ACCOUNT_RESULT, FakeMcp, FakeModel, FixedClock, make_limits

from ezchain_agent_runtime import AgentLoop, Answer, ModelTurn, ToolCall
from ezchain_agent_runtime.trace import (
    FileTraceLog,
    MemoryTraceLog,
    TraceEvent,
    TraceEventKind,
    TraceRecorder,
    TraceSink,
    TraceStoreError,
    TraceWriteFailed,
)

PACKAGE = Path(__file__).resolve().parents[2] / "services" / "agent-runtime" / "src"
TRACE_MODULE = PACKAGE / "ezchain_agent_runtime" / "trace.py"

MUTATING_NAME = re.compile(r"update|delete|remove|truncate|rewrite|replace|clear|drop|patch|del")


def _public_members(cls: type) -> set[str]:
    return {name for name in dir(cls) if not name.startswith("_")}


def _open_modes(source: Path) -> set[str]:
    modes: set[str] = set()
    for node in ast.walk(ast.parse(source.read_text())):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "open":
            continue
        for keyword in node.keywords:
            if keyword.arg == "mode":
                modes.add(str(ast.literal_eval(keyword.value)))
        if node.args:
            modes.add(str(ast.literal_eval(node.args[0])))
    return modes


def test_the_sink_interface_exposes_only_append() -> None:
    assert _public_members(TraceSink) == {"append"}


def test_no_store_has_a_mutating_operation() -> None:
    assert _public_members(MemoryTraceLog) == {"append", "read", "sessions"}
    assert _public_members(FileTraceLog) == {"append", "path_for", "read", "sessions"}
    for store in (MemoryTraceLog, FileTraceLog):
        assert [name for name in _public_members(store) if MUTATING_NAME.search(name)] == []


def test_the_file_store_opens_a_trace_only_to_append() -> None:
    assert TRACE_MODULE.is_file()
    assert _open_modes(TRACE_MODULE) == {"a"}


def test_the_trace_module_never_writes_a_file_whole() -> None:
    called = {
        node.func.attr
        for node in ast.walk(ast.parse(TRACE_MODULE.read_text()))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert called.isdisjoint({"write_text", "touch", "unlink", "rmdir", "rename", "rmtree"})


def test_a_session_file_is_refused_once_it_has_events(tmp_path: Path) -> None:
    first = FileTraceLog(tmp_path)
    TraceRecorder(session_id="s1", sink=first, clock=FixedClock()).session_start(
        prompt="what is my equity?", model_id="model-x"
    )

    second = FileTraceLog(tmp_path)
    with pytest.raises(TraceWriteFailed) as caught:
        TraceRecorder(session_id="s1", sink=second, clock=FixedClock()).session_start(
            prompt="a second run", model_id="model-x"
        )

    assert isinstance(caught.value.__cause__, TraceStoreError)
    assert "s1" in str(caught.value)


def test_a_store_refuses_an_event_that_does_not_extend_the_chain() -> None:
    store = MemoryTraceLog()
    recorder = TraceRecorder(session_id="s1", sink=store, clock=FixedClock())
    recorder.session_start(prompt="go", model_id="model-x")
    event = store.read("s1")[0]

    with pytest.raises(TraceStoreError):
        store.append(event)
    assert len(store.read("s1")) == 1


def test_a_refused_append_leaves_no_session_behind(tmp_path: Path) -> None:
    """A refused event must not change what the store holds — no session, no file."""
    stray = TraceEvent.create(
        session_id="s2",
        sequence=0,
        kind=TraceEventKind.SESSION_START,
        at=datetime(2026, 11, 14, tzinfo=UTC),
        fields={},
        previous_hash="f" * 64,
    )

    memory = MemoryTraceLog()
    with pytest.raises(TraceStoreError):
        memory.append(stray)
    assert memory.sessions() == ()
    with pytest.raises(TraceStoreError):
        memory.read("s2")

    files = FileTraceLog(tmp_path)
    with pytest.raises(TraceStoreError):
        files.append(stray)
    assert files.sessions() == ()
    with pytest.raises(TraceStoreError):
        files.read("s2")


def test_a_session_id_cannot_escape_the_trace_directory(tmp_path: Path) -> None:
    store = FileTraceLog(tmp_path)

    with pytest.raises(TraceStoreError):
        store.path_for("../escape")


def test_a_sink_that_cannot_append_stops_the_session_before_the_first_turn() -> None:
    class Broken:
        def append(self, event: TraceEvent) -> None:
            raise TraceStoreError("disk full")

    model = FakeModel([ModelTurn(answer=Answer(summary="never"))])
    loop = AgentLoop(
        model=model,
        toolbox=FakeMcp(),
        limits=make_limits(),
        clock=FixedClock(),
        trace=Broken(),
    )

    with pytest.raises(TraceWriteFailed):
        loop.run("hello")
    assert model.requests == []


def test_one_recorded_session_is_one_append_only_file(tmp_path: Path) -> None:
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

    path = tmp_path / f"{outcome.session_id}.jsonl"
    assert path.is_file()
    events = FileTraceLog(tmp_path).read(outcome.session_id)
    assert [event.kind.value for event in events] == [
        "session_start",
        "kill_switch",
        "turn",
        "budget_debit",
        "kill_switch",
        "tool_call",
        "kill_switch",
        "turn",
        "budget_debit",
        "outcome",
    ]
    assert {event.session_id for event in events} == {outcome.session_id}
    assert [event.sequence for event in events] == list(range(len(events)))
    assert path.read_text().count("\n") == len(events)
