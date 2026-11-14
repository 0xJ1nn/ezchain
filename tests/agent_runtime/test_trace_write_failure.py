"""A trace that cannot be written stops the session, and bills nothing it did not record.

The loop used to charge the budget before writing the debit, so a failed trace left the day
charged and the file ending at a ``turn`` with no outcome — a session that paid for a turn its
audit trail never saw, and a prefix ``replay`` refuses. The debit now comes first; these tests
pin both halves.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from agent_helpers import FakeMcp, FakeModel, FixedClock, make_limits

from ezchain_agent_runtime import (
    AgentLoop,
    Answer,
    DailyLedger,
    ModelTurn,
    ModelUsage,
    TraceIncomplete,
    TraceStoreError,
    TraceWriteFailed,
    replay,
)
from ezchain_agent_runtime.trace import FileTraceLog, MemoryTraceLog


class FailOnKind(MemoryTraceLog):
    """A memory store that refuses the first event of one kind, after everything before it."""

    def __init__(self, kind: str) -> None:
        super().__init__()
        self._kind = kind

    def append(self, event: object) -> None:
        if getattr(event, "kind", None) == self._kind:
            raise TraceStoreError(f"disk full while writing a {self._kind}")
        super().append(event)  # type: ignore[arg-type]


def test_a_failed_budget_debit_charges_the_day_nothing() -> None:
    clock = FixedClock()
    daily = DailyLedger(clock=clock)
    store = FailOnKind("budget_debit")
    model = FakeModel(
        [
            ModelTurn(
                answer=Answer(summary="nothing to report"),
                usage=ModelUsage(prompt_tokens=7, completion_tokens=3, cost_usd=Decimal("0.02")),
            )
        ]
    )
    loop = AgentLoop(
        model=model,
        toolbox=FakeMcp(),
        limits=make_limits(),
        daily=daily,
        clock=clock,
        trace=store,
    )

    with pytest.raises(TraceWriteFailed):
        loop.run("hello")

    assert daily.used().turns == 0
    assert daily.used().tokens == 0
    assert daily.used().cost_usd == Decimal("0")
    (session_id,) = store.sessions()
    assert [event.kind.value for event in store.read(session_id)] == [
        "session_start",
        "kill_switch",
        "turn",
    ]


def test_a_failed_debit_leaves_a_dangling_prefix_that_replay_refuses() -> None:
    store = FailOnKind("budget_debit")
    model = FakeModel(
        [
            ModelTurn(
                answer=Answer(summary="nothing to report"),
                usage=ModelUsage(prompt_tokens=7, completion_tokens=3, cost_usd=Decimal("0.02")),
            )
        ]
    )
    loop = AgentLoop(
        model=model,
        toolbox=FakeMcp(),
        limits=make_limits(),
        clock=FixedClock(),
        trace=store,
    )

    with pytest.raises(TraceWriteFailed):
        loop.run("hello")

    (session_id,) = store.sessions()
    events = store.read(session_id)
    assert [event.kind.value for event in events] == ["session_start", "kill_switch", "turn"]
    with pytest.raises(TraceIncomplete, match="no recorded outcome"):
        replay(events)


def test_a_turn_is_debited_before_it_is_charged(tmp_path: Path) -> None:
    """A successful recording still records the totals the charge produces."""
    clock = FixedClock()
    daily = DailyLedger(clock=clock)
    model = FakeModel(
        [
            ModelTurn(
                answer=Answer(summary="nothing to report"),
                usage=ModelUsage(prompt_tokens=7, completion_tokens=3, cost_usd=Decimal("0.02")),
            )
        ]
    )
    loop = AgentLoop(
        model=model,
        toolbox=FakeMcp(),
        limits=make_limits(),
        daily=daily,
        clock=clock,
        trace=FileTraceLog(tmp_path),
    )
    outcome = loop.run("hello")

    events = FileTraceLog(tmp_path).read(outcome.session_id)
    debit = next(event for event in events if event.kind.value == "budget_debit")
    assert debit.fields["session"] == {"turns": 1, "tokens": 10, "cost_usd": "0.02"}
    assert debit.fields["daily"] == {"turns": 1, "tokens": 10, "cost_usd": "0.02"}
    assert daily.used().tokens == 10


def test_a_turn_that_cannot_be_debited_was_already_traced(tmp_path: Path) -> None:
    """The turn itself is on disk before the debit, so the prefix names the work it paid for."""
    store = FailOnKind("budget_debit")
    model = FakeModel(
        [
            ModelTurn(
                answer=Answer(summary="nothing to report"),
                usage=ModelUsage(prompt_tokens=1, completion_tokens=1),
            )
        ]
    )
    with pytest.raises(TraceWriteFailed):
        AgentLoop(
            model=model,
            toolbox=FakeMcp(),
            limits=make_limits(),
            clock=FixedClock(),
            trace=store,
        ).run("hello")

    (session_id,) = store.sessions()
    assert store.read(session_id)[-1].fields["tokens"] == 2
