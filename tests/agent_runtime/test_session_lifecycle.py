"""The six-state lifecycle, declared in one table and used by the loop."""

from __future__ import annotations

import pytest
from agent_helpers import (
    APPROVALS,
    FakeMcp,
    FakeModel,
    FixedClock,
    make_limits,
)

from ezchain_agent_runtime import (
    TERMINAL_STATES,
    TRANSITIONS,
    AgentLoop,
    Answer,
    InvalidTransition,
    ModelTurn,
    Session,
    SessionEvent,
    SessionState,
    StopReason,
    ToolCall,
    ToolResult,
)


def test_the_lifecycle_declares_exactly_the_six_states() -> None:
    assert {state.value for state in SessionState} == {
        "idle",
        "running",
        "awaiting_approval",
        "done",
        "stopped",
        "failed",
    }
    assert TERMINAL_STATES == {
        SessionState.DONE,
        SessionState.STOPPED,
        SessionState.FAILED,
    }


def test_every_state_is_reachable_from_idle_through_the_table() -> None:
    reached = {SessionState.IDLE}
    changed = True
    while changed:
        changed = False
        for (state, _event), nxt in TRANSITIONS.items():
            if state in reached and nxt not in reached:
                reached.add(nxt)
                changed = True
    assert reached == set(SessionState)


def test_terminal_states_have_no_outgoing_row() -> None:
    assert {state for state, _event in TRANSITIONS}.isdisjoint(TERMINAL_STATES)


def test_only_the_table_decides_a_transition() -> None:
    session = Session("s1")
    assert session.fire(SessionEvent.START) is SessionState.RUNNING
    assert session.fire(SessionEvent.FINISH) is SessionState.DONE
    with pytest.raises(InvalidTransition):
        session.fire(SessionEvent.START)

    idle = Session("s2")
    with pytest.raises(InvalidTransition):
        idle.fire(SessionEvent.FINISH)


def test_awaiting_approval_resumes_only_when_a_person_decided() -> None:
    session = Session("s1")
    session.fire(SessionEvent.START)
    assert session.fire(SessionEvent.APPROVAL_REQUIRED) is SessionState.AWAITING_APPROVAL
    assert session.fire(SessionEvent.APPROVAL_DECIDED) is SessionState.RUNNING


def test_a_pending_order_puts_the_session_into_awaiting_approval() -> None:
    approvals = ToolResult(
        tool=APPROVALS.name,
        data={
            "id": "o1",
            "status": "pending_approval",
            "approval_id": "a1",
            "approval_status": "pending",
        },
    )
    model = FakeModel(
        [ModelTurn(tool_calls=(ToolCall(id="c1", name=APPROVALS.name, arguments={}),))]
    )
    outcome = AgentLoop(
        model=model,
        toolbox=FakeMcp(results={APPROVALS.name: approvals}),
        limits=make_limits(),
        clock=FixedClock(),
    ).run("place the order")

    assert outcome.state is SessionState.AWAITING_APPROVAL
    assert outcome.reason is StopReason.AWAITING_APPROVAL
    assert outcome.answer is None


def test_the_happy_path_moves_idle_running_done() -> None:
    outcome = AgentLoop(
        model=FakeModel([ModelTurn(answer=Answer(summary="nothing to report"))]),
        toolbox=FakeMcp(),
        limits=make_limits(),
        clock=FixedClock(),
    ).run("hello")

    assert outcome.state is SessionState.DONE
    assert outcome.reason is StopReason.COMPLETED
