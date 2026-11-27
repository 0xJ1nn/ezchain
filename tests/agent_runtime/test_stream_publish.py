"""The trace tail forwards a real run's recorded events without owning them.

Nothing here fills a store by hand. A scripted model and a fake MCP server drive the real
:class:`~ezchain_agent_runtime.AgentLoop`, and the tail is the sink the loop writes to, so every
forwarded event is one the store actually appended, in the order it was written.
"""

from __future__ import annotations

import pytest
from agent_helpers import FakeMcp, FakeModel, FixedClock, make_limits

from ezchain_agent_runtime import (
    AgentLoop,
    Answer,
    MemoryTraceLog,
    ModelTurn,
    RuntimeConfig,
)
from ezchain_agent_runtime.trace import TraceStoreError, TraceTail, TraceWriteFailed

SESSION = "session-tail"


def _run(tail: TraceTail) -> str:
    model = FakeModel([ModelTurn(answer=Answer(summary="done"))])
    loop = AgentLoop(
        model=model,
        toolbox=FakeMcp(),
        limits=make_limits(),
        clock=FixedClock(),
        trace=tail,
        config=RuntimeConfig(session_id_factory=lambda: SESSION),
    )
    return loop.run("watch me").session_id


def test_the_tail_forwards_exactly_the_events_the_store_appended() -> None:
    store = MemoryTraceLog()
    tail = TraceTail(store)
    seen = []
    tail.listen(seen.append)

    session_id = _run(tail)

    recorded = list(store.read(session_id))
    assert seen == recorded
    assert [event.sequence for event in seen] == list(range(len(recorded)))


def test_two_listeners_see_the_same_order() -> None:
    tail = TraceTail(MemoryTraceLog())
    first, second = [], []
    tail.listen(first.append)
    tail.listen(second.append)

    _run(tail)

    assert [event.digest for event in first] == [event.digest for event in second]


def test_unlistening_stops_the_fanout() -> None:
    tail = TraceTail(MemoryTraceLog())
    seen = []
    tail.listen(seen.append)

    tail.unlisten(seen.append)
    _run(tail)

    assert seen == []


def test_a_failing_listener_cannot_stop_the_run_or_the_store() -> None:
    store = MemoryTraceLog()
    tail = TraceTail(store)

    def broken(event: object) -> None:
        raise RuntimeError("observer went away")

    tail.listen(broken)
    session_id = _run(tail)

    assert len(store.read(session_id)) >= 2


def test_an_event_the_store_refused_is_never_forwarded() -> None:
    class RefusingStore:
        key = None

        def __init__(self) -> None:
            self.calls = 0

        def append(self, event: object) -> None:
            self.calls += 1
            raise TraceStoreError("the trace is unavailable")

    store = RefusingStore()
    tail = TraceTail(store)  # type: ignore[arg-type]
    seen = []
    tail.listen(seen.append)

    with pytest.raises(TraceWriteFailed):
        _run(tail)

    assert store.calls >= 1
    assert seen == []
