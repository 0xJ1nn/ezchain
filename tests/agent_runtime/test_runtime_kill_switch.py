from __future__ import annotations

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
    InMemoryKillSwitch,
    ModelTurn,
    SessionState,
    StopReason,
    ToolCall,
    ToolResult,
)

TWO_CALLS = ModelTurn(
    tool_calls=(
        ToolCall(id="c1", name=ACCOUNT.name, arguments={}),
        ToolCall(id="c2", name=APPROVALS.name, arguments={}),
    )
)


def _loop(model: FakeModel, mcp: FakeMcp, switch: InMemoryKillSwitch) -> AgentLoop:
    return AgentLoop(
        model=model,
        toolbox=mcp,
        limits=make_limits(),
        kill_switch=switch,
        clock=FixedClock(),
    )


def test_an_armed_switch_stops_a_session_before_its_first_turn() -> None:
    switch = InMemoryKillSwitch(armed=True, reason="operator stopped the desk")
    model = FakeModel([ModelTurn(answer=None)])
    outcome = _loop(model, FakeMcp(results={ACCOUNT.name: ACCOUNT_RESULT}), switch).run("go")

    assert outcome.state is SessionState.STOPPED
    assert outcome.reason is StopReason.KILL_SWITCH_ARMED
    assert outcome.detail == "operator stopped the desk"
    assert model.requests == []


def test_arming_mid_session_stops_before_the_next_tool_call() -> None:
    switch = InMemoryKillSwitch()
    mcp = FakeMcp(
        results={
            ACCOUNT.name: ACCOUNT_RESULT,
            APPROVALS.name: ToolResult(tool=APPROVALS.name, data=[]),
        },
        on_call=lambda name, _arguments: switch.arm("armed by the operator"),
    )
    outcome = _loop(FakeModel([TWO_CALLS]), mcp, switch).run("go")

    assert outcome.reason is StopReason.KILL_SWITCH_ARMED
    assert outcome.state is SessionState.STOPPED
    assert [name for name, _ in mcp.calls] == [ACCOUNT.name]
    assert len(outcome.observations) == 1


def test_a_platform_kill_switch_refusal_stops_the_session_on_that_tool_result() -> None:
    mcp = FakeMcp(
        results={
            ACCOUNT.name: ToolResult(
                tool=ACCOUNT.name,
                data={"code": "KILL_SWITCH_ACTIVE", "message": "entry refused"},
                is_error=True,
            ),
            APPROVALS.name: ToolResult(tool=APPROVALS.name, data=[]),
        }
    )
    outcome = _loop(FakeModel([TWO_CALLS]), mcp, InMemoryKillSwitch()).run("place the order")

    assert outcome.state is SessionState.STOPPED
    assert outcome.reason is StopReason.KILL_SWITCH_ACTIVE
    assert [name for name, _ in mcp.calls] == [ACCOUNT.name]
