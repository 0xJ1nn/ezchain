"""Replay: the trace alone reproduces the session summary.

A trace that cannot answer "what did this session do, and how did it end" is a log, not a
record. :func:`replay` takes the events and nothing else — no loop, no model, no ledger — and
must produce exactly what :func:`summarize` reads off the live outcome.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from agent_helpers import (
    ACCOUNT,
    ACCOUNT_RESULT,
    APPROVALS,
    FakeMcp,
    FakeModel,
    FixedClock,
    make_limits,
)

from ezchain_agent_runtime import (
    AgentLoop,
    Answer,
    InMemoryKillSwitch,
    ModelTurn,
    ModelUsage,
    NumberClaim,
    SessionState,
    StopReason,
    ToolCall,
    ToolResult,
    TraceChainBroken,
    TraceEvent,
    TraceIncomplete,
    replay,
    summarize,
    verify_events,
)
from ezchain_agent_runtime.trace import FileTraceLog

TOOL_TURN = ModelTurn(
    tool_calls=(ToolCall(id="c1", name=ACCOUNT.name, arguments={"account": "main"}),),
    usage=ModelUsage(prompt_tokens=11, completion_tokens=5, cost_usd=Decimal("0.03")),
)


def _record(
    tmp_path: Path,
    model: FakeModel,
    *,
    results: dict[str, ToolResult] | None = None,
    switch: InMemoryKillSwitch | None = None,
) -> tuple[Path, object]:
    loop = AgentLoop(
        model=model,
        toolbox=FakeMcp(results=results or {ACCOUNT.name: ACCOUNT_RESULT}),
        limits=make_limits(),
        kill_switch=switch,
        clock=FixedClock(),
        trace=FileTraceLog(tmp_path),
    )
    outcome = loop.run("what is my equity?")
    return tmp_path, outcome


def _replayed(root: Path, session_id: str) -> object:
    return replay(FileTraceLog(root).read(session_id))


def test_a_completed_session_replays_to_its_live_summary(tmp_path: Path) -> None:
    model = FakeModel(
        [
            TOOL_TURN,
            ModelTurn(
                answer=Answer(
                    summary="Read from the account.",
                    claims=(
                        NumberClaim(
                            label="equity",
                            value=Decimal("1000.00"),
                            tool=ACCOUNT.name,
                            path="equity",
                        ),
                    ),
                ),
                usage=ModelUsage(prompt_tokens=20, completion_tokens=4, cost_usd=Decimal("0.02")),
            ),
        ]
    )
    root, outcome = _record(tmp_path, model)

    assert _replayed(root, outcome.session_id) == summarize(outcome)
    summary = _replayed(root, outcome.session_id)
    assert summary.state is SessionState.DONE
    assert summary.reason is StopReason.COMPLETED
    assert (summary.turns, summary.tool_calls, summary.tool_errors) == (2, 1, 0)
    assert summary.tokens == 40
    assert summary.cost_usd == Decimal("0.05")
    assert summary.numbers == 1


def test_a_budget_stop_replays_to_its_live_summary(tmp_path: Path) -> None:
    model = FakeModel(
        [
            ModelTurn(
                tool_calls=(ToolCall(id="c1", name=ACCOUNT.name, arguments={}),),
                usage=ModelUsage(cost_usd=Decimal("5.00")),
            )
        ]
    )
    loop = AgentLoop(
        model=model,
        toolbox=FakeMcp(results={ACCOUNT.name: ACCOUNT_RESULT}),
        limits=make_limits(session_cost_usd=Decimal("0.01")),
        clock=FixedClock(),
        trace=FileTraceLog(tmp_path),
    )
    outcome = loop.run("equity?")

    assert _replayed(tmp_path, outcome.session_id) == summarize(outcome)
    assert summarize(outcome).reason is StopReason.SESSION_COST_EXHAUSTED


def test_a_kill_switch_stop_replays_to_its_live_summary(tmp_path: Path) -> None:
    switch = InMemoryKillSwitch(armed=True, reason="operator stopped the desk")
    root, outcome = _record(
        tmp_path, FakeModel([ModelTurn(answer=Answer(summary="never"))]), switch=switch
    )

    summary = _replayed(root, outcome.session_id)

    assert summary == summarize(outcome)
    assert summary.reason is StopReason.KILL_SWITCH_ARMED
    assert summary.detail == "operator stopped the desk"
    assert summary.turns == 0


def test_an_approval_stop_replays_to_its_live_summary(tmp_path: Path) -> None:
    pending = ToolResult(
        tool=APPROVALS.name,
        data={"status": "pending_approval", "approval_id": "ap-1"},
    )
    model = FakeModel(
        [ModelTurn(tool_calls=(ToolCall(id="c1", name=APPROVALS.name, arguments={}),))]
    )
    root, outcome = _record(
        tmp_path,
        model,
        results={APPROVALS.name: pending},
    )

    summary = _replayed(root, outcome.session_id)

    assert summary == summarize(outcome)
    assert summary.state is SessionState.AWAITING_APPROVAL
    events = FileTraceLog(root).read(outcome.session_id)
    assert events[-1].fields["approval_id"] == "ap-1"


def test_a_tool_error_is_counted_in_replay(tmp_path: Path) -> None:
    refused = ToolResult(
        tool=ACCOUNT.name,
        data={"code": "FORBIDDEN", "message": "no scope"},
        is_error=True,
    )
    model = FakeModel(
        [
            TOOL_TURN,
            ModelTurn(answer=Answer(summary="The account read was refused.")),
        ]
    )
    root, outcome = _record(tmp_path, model, results={ACCOUNT.name: refused})

    summary = _replayed(root, outcome.session_id)

    assert summary == summarize(outcome)
    assert (summary.tool_calls, summary.tool_errors) == (1, 1)


def test_a_model_failure_replays_to_its_live_summary(tmp_path: Path) -> None:
    root, outcome = _record(tmp_path, FakeModel([RuntimeError("provider down")]))

    assert _replayed(root, outcome.session_id) == summarize(outcome)
    assert summarize(outcome).reason is StopReason.MODEL_FAILED


def test_a_refused_tool_name_replays_to_its_live_summary(tmp_path: Path) -> None:
    model = FakeModel(
        [ModelTurn(tool_calls=(ToolCall(id="c1", name="shell", arguments={"cmd": "ls"}),))]
    )
    root, outcome = _record(tmp_path, model)

    assert _replayed(root, outcome.session_id) == summarize(outcome)
    assert summarize(outcome).reason is StopReason.TOOL_OUT_OF_SCOPE
    assert summarize(outcome).tool_calls == 0


def test_an_unreachable_server_replays_to_its_live_summary(tmp_path: Path) -> None:
    loop = AgentLoop(
        model=FakeModel([ModelTurn(answer=Answer(summary="never"))]),
        toolbox=FakeMcp(list_error=RuntimeError("child exited")),
        limits=make_limits(),
        clock=FixedClock(),
        trace=FileTraceLog(tmp_path),
    )
    outcome = loop.run("hello")

    assert _replayed(tmp_path, outcome.session_id) == summarize(outcome)
    assert summarize(outcome).reason is StopReason.MCP_UNREACHABLE


def test_a_trace_without_an_outcome_is_not_replayable(tmp_path: Path) -> None:
    loop = AgentLoop(
        model=FakeModel([TOOL_TURN, ModelTurn(answer=Answer(summary="done"))]),
        toolbox=FakeMcp(results={ACCOUNT.name: ACCOUNT_RESULT}),
        limits=make_limits(),
        clock=FixedClock(),
        trace=FileTraceLog(tmp_path),
    )
    outcome = loop.run("equity?")
    events = FileTraceLog(tmp_path).read(outcome.session_id)

    with pytest.raises(TraceIncomplete):
        replay(events[:-1])


def test_a_trace_with_two_outcomes_is_not_replayable(tmp_path: Path) -> None:
    root, outcome = _record(
        tmp_path, FakeModel([ModelTurn(answer=Answer(summary="nothing to report"))])
    )
    events = FileTraceLog(root).read(outcome.session_id)
    last = events[-1]
    second = TraceEvent.create(
        session_id=last.session_id,
        sequence=last.sequence + 1,
        kind=last.kind,
        at=last.at,
        fields=last.fields,
        previous_hash=last.digest,
    )

    with pytest.raises(TraceIncomplete):
        replay((*events, second))


def test_a_tampered_trace_is_refused_before_it_is_replayed(tmp_path: Path) -> None:
    root, outcome = _record(
        tmp_path, FakeModel([ModelTurn(answer=Answer(summary="nothing to report"))])
    )
    events = FileTraceLog(root).read(outcome.session_id)
    broken = TraceEvent(
        session_id=events[1].session_id,
        sequence=events[1].sequence,
        kind=events[1].kind,
        at=events[1].at,
        fields={**events[1].fields, "armed": True},
        previous_hash=events[1].previous_hash,
        digest=events[1].digest,
    )

    with pytest.raises(TraceChainBroken):
        replay((events[0], broken, *events[2:]))


def test_verification_passes_on_every_recorded_session(tmp_path: Path) -> None:
    root, outcome = _record(
        tmp_path, FakeModel([ModelTurn(answer=Answer(summary="nothing to report"))])
    )

    events = FileTraceLog(root).read(outcome.session_id)
    assert verify_events(events) is None
