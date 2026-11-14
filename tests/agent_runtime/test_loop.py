"""The loop itself: turns, tool calls, failures and the message it builds."""

from __future__ import annotations

from decimal import Decimal

from agent_helpers import (
    ACCOUNT,
    ACCOUNT_RESULT,
    FakeMcp,
    FakeModel,
    FixedClock,
    make_limits,
)

from ezchain_agent_runtime import (
    AgentLoop,
    Answer,
    ModelTurn,
    NumberClaim,
    SessionState,
    StopReason,
    ToolCall,
    ToolResult,
)


def _loop(model: FakeModel, mcp: FakeMcp) -> AgentLoop:
    return AgentLoop(
        model=model,
        toolbox=mcp,
        limits=make_limits(),
        clock=FixedClock(),
    )


def test_a_tool_result_feeds_the_next_turn_and_the_answer_is_sourced() -> None:
    model = FakeModel(
        [
            ModelTurn(tool_calls=(ToolCall(id="c1", name=ACCOUNT.name, arguments={}),)),
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
                )
            ),
        ]
    )
    outcome = _loop(model, FakeMcp(results={ACCOUNT.name: ACCOUNT_RESULT})).run("equity?")

    assert outcome.state is SessionState.DONE
    assert outcome.reason is StopReason.COMPLETED
    assert outcome.answer is not None
    assert outcome.answer.numbers[0].tool == ACCOUNT.name
    second = model.requests[1]
    roles = [message.role for message in second.messages]
    assert roles == ["system", "user", "assistant", "tool"]
    assert second.messages[2].tool_calls[0].name == ACCOUNT.name
    assert '"equity":"1000.00"' in second.messages[3].content
    assert len(outcome.observations) == 1


def test_an_answer_with_no_tool_calls_finishes_in_one_turn() -> None:
    model = FakeModel([ModelTurn(answer=Answer(summary="nothing to report"))])
    outcome = _loop(model, FakeMcp()).run("hello")

    assert outcome.state is SessionState.DONE
    assert outcome.usage.turns == 1


def test_a_model_failure_fails_the_session() -> None:
    outcome = _loop(FakeModel([RuntimeError("provider down")]), FakeMcp()).run("hello")

    assert outcome.state is SessionState.FAILED
    assert outcome.reason is StopReason.MODEL_FAILED
    assert "provider down" in (outcome.detail or "")


def test_a_model_turn_with_neither_calls_nor_answer_fails() -> None:
    outcome = _loop(FakeModel([ModelTurn(text="thinking")]), FakeMcp()).run("hello")

    assert outcome.state is SessionState.FAILED
    assert outcome.reason is StopReason.MODEL_FAILED


def test_a_model_turn_with_both_calls_and_answer_fails() -> None:
    turn = ModelTurn(
        tool_calls=(ToolCall(id="c1", name=ACCOUNT.name, arguments={}),),
        answer=Answer(summary="done"),
    )
    outcome = _loop(FakeModel([turn]), FakeMcp(results={ACCOUNT.name: ACCOUNT_RESULT})).run("hello")

    assert outcome.state is SessionState.FAILED
    assert outcome.reason is StopReason.MODEL_FAILED


def test_an_exception_from_a_tool_call_fails_the_session_closed() -> None:
    mcp = FakeMcp(results={ACCOUNT.name: RuntimeError("child died")})
    model = FakeModel([ModelTurn(tool_calls=(ToolCall(id="c1", name=ACCOUNT.name, arguments={}),))])
    outcome = _loop(model, mcp).run("equity?")

    assert outcome.state is SessionState.FAILED
    assert outcome.reason is StopReason.MCP_UNREACHABLE


def test_a_tool_error_is_fed_back_to_the_model_and_the_session_continues() -> None:
    refused = ToolResult(
        tool=ACCOUNT.name,
        data={"code": "FORBIDDEN", "message": "no scope"},
        is_error=True,
    )
    model = FakeModel(
        [
            ModelTurn(tool_calls=(ToolCall(id="c1", name=ACCOUNT.name, arguments={}),)),
            ModelTurn(answer=Answer(summary="The account read was refused.")),
        ]
    )
    outcome = _loop(model, FakeMcp(results={ACCOUNT.name: refused})).run("equity?")

    assert outcome.state is SessionState.DONE
    assert len(outcome.observations) == 1
    assert outcome.observations[0].is_error is True


def test_the_request_carries_the_system_prompt_the_user_prompt_and_the_surface() -> None:
    model = FakeModel([ModelTurn(answer=Answer(summary="nothing to report"))])
    _loop(model, FakeMcp()).run("what changed today?")

    request = model.requests[0]
    assert request.messages[0].role == "system"
    assert "no such tool exists" in request.messages[0].content
    assert request.messages[1].role == "user"
    assert request.messages[1].content == "what changed today?"
    assert [item["name"] for item in request.tools] == [tool.name for tool in FakeMcp().tools]
