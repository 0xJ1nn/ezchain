from __future__ import annotations

from decimal import Decimal
from typing import Any

from agent_helpers import ACCOUNT, ACCOUNT_RESULT, FakeMcp, FakeModel, FixedClock, make_limits

from ezchain_agent_runtime import (
    AgentLoop,
    Answer,
    DailyLedger,
    ModelTurn,
    ModelUsage,
    SessionState,
    StopReason,
    ToolCall,
)

TURN_WITH_TOOL = ModelTurn(
    tool_calls=(ToolCall(id="c1", name=ACCOUNT.name, arguments={}),),
)


def _loop(
    limits: dict[str, Any],
    model: FakeModel,
    *,
    daily: DailyLedger | None = None,
    clock: FixedClock | None = None,
) -> AgentLoop:
    return AgentLoop(
        model=model,
        toolbox=FakeMcp(results={ACCOUNT.name: ACCOUNT_RESULT}),
        limits=make_limits(**limits),
        daily=daily,
        clock=clock or FixedClock(),
    )


def test_session_turn_budget_stops_the_loop_after_the_last_allowed_turn() -> None:
    model = FakeModel([TURN_WITH_TOOL, TURN_WITH_TOOL, TURN_WITH_TOOL])
    outcome = _loop({"session_turns": 1}, model).run("what is my equity?")

    assert outcome.state is SessionState.STOPPED
    assert outcome.reason is StopReason.SESSION_TURNS_EXHAUSTED
    assert outcome.usage.turns == 1
    assert len(model.requests) == 1


def test_session_token_budget_stops_before_the_next_tool_call() -> None:
    model = FakeModel(
        [
            ModelTurn(
                tool_calls=(ToolCall(id="c1", name=ACCOUNT.name, arguments={}),),
                usage=ModelUsage(prompt_tokens=4, completion_tokens=4),
            ),
            TURN_WITH_TOOL,
        ]
    )
    outcome = _loop({"session_tokens": 8}, model).run("read the account")

    assert outcome.state is SessionState.STOPPED
    assert outcome.reason is StopReason.SESSION_TOKENS_EXHAUSTED
    assert outcome.usage.tokens == 8
    assert outcome.observations == ()


def test_session_cost_budget_stops_the_loop() -> None:
    model = FakeModel(
        [
            ModelTurn(
                tool_calls=(ToolCall(id="c1", name=ACCOUNT.name, arguments={}),),
                usage=ModelUsage(prompt_tokens=1, completion_tokens=1, cost_usd=Decimal("0.25")),
            )
        ]
    )
    outcome = _loop({"session_cost_usd": Decimal("0.25")}, model).run("read the account")

    assert outcome.state is SessionState.STOPPED
    assert outcome.reason is StopReason.SESSION_COST_EXHAUSTED
    assert outcome.usage.cost_usd == Decimal("0.25")


def test_daily_turn_budget_stops_a_session_that_starts_after_the_limit() -> None:
    clock = FixedClock()
    daily = DailyLedger(make_limits(daily_turns=1), clock=clock)
    limits = {"daily_turns": 1}
    first = _loop(
        limits,
        FakeModel([ModelTurn(answer=Answer(summary="nothing to report"))]),
        daily=daily,
        clock=clock,
    ).run("hello")
    assert first.state is SessionState.DONE

    model = FakeModel([ModelTurn(answer=Answer(summary="nothing to report"))])
    second = _loop(limits, model, daily=daily, clock=clock).run("hello again")

    assert second.state is SessionState.STOPPED
    assert second.reason is StopReason.DAILY_TURNS_EXHAUSTED
    assert model.requests == []


def test_daily_token_budget_stops_the_loop() -> None:
    model = FakeModel(
        [
            ModelTurn(
                tool_calls=(ToolCall(id="c1", name=ACCOUNT.name, arguments={}),),
                usage=ModelUsage(prompt_tokens=30, completion_tokens=30),
            )
        ]
    )
    outcome = _loop({"daily_tokens": 50}, model).run("read the account")

    assert outcome.reason is StopReason.DAILY_TOKENS_EXHAUSTED
    assert outcome.state is SessionState.STOPPED


def test_daily_cost_budget_stops_the_loop() -> None:
    model = FakeModel(
        [
            ModelTurn(
                tool_calls=(ToolCall(id="c1", name=ACCOUNT.name, arguments={}),),
                usage=ModelUsage(cost_usd=Decimal("0.05")),
            )
        ]
    )
    outcome = _loop({"daily_cost_usd": Decimal("0.01")}, model).run("read the account")

    assert outcome.reason is StopReason.DAILY_COST_EXHAUSTED
    assert outcome.state is SessionState.STOPPED
    assert outcome.daily_usage.cost_usd == Decimal("0.05")


def test_a_completed_session_carries_its_spend_into_the_daily_ledger() -> None:
    clock = FixedClock()
    limits = make_limits()
    daily = DailyLedger(limits, clock=clock)
    loop = AgentLoop(
        model=FakeModel(
            [
                ModelTurn(
                    answer=Answer(summary="nothing to report"),
                    usage=ModelUsage(
                        prompt_tokens=7, completion_tokens=3, cost_usd=Decimal("0.02")
                    ),
                )
            ]
        ),
        toolbox=FakeMcp(),
        limits=limits,
        daily=daily,
        clock=clock,
    )
    outcome = loop.run("hello")

    assert outcome.state is SessionState.DONE
    assert daily.used().tokens == 10
    assert daily.used().cost_usd == Decimal("0.02")
