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
    agent: str = "default",
) -> AgentLoop:
    return AgentLoop(
        model=model,
        toolbox=FakeMcp(results={ACCOUNT.name: ACCOUNT_RESULT}),
        limits=make_limits(**limits),
        daily=daily,
        agent=agent,
        clock=clock or FixedClock(),
    )


def _cost_model(cost: Decimal) -> FakeModel:
    """One turn that returns an answer and costs ``cost``."""
    return FakeModel(
        [ModelTurn(answer=Answer(summary="nothing to report"), usage=ModelUsage(cost_usd=cost))]
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
    daily = DailyLedger(clock=clock)
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


def test_four_sessions_in_one_day_spend_from_one_daily_budget() -> None:
    clock = FixedClock()
    limits = {"daily_cost_usd": Decimal("1.00")}
    first_three = [
        _loop(limits, _cost_model(Decimal("0.30")), clock=clock).run("hello") for _ in range(3)
    ]
    fourth_model = _cost_model(Decimal("0.30"))
    fourth = _loop(limits, fourth_model, clock=clock).run("hello again")

    assert [outcome.reason for outcome in first_three] == [StopReason.COMPLETED] * 3
    assert fourth.state is SessionState.STOPPED
    assert fourth.reason is StopReason.DAILY_COST_EXHAUSTED
    assert fourth.answer is None
    assert fourth.daily_usage.cost_usd == Decimal("1.20")


def test_three_sessions_in_one_day_share_one_daily_budget() -> None:
    """The review's probe: three loops, one day, a 1.00 cap, 0.90 each.

    The third must refuse, and the second — which crosses the cap while spending — stops
    rather than reporting COMPLETED (finding 2).
    """
    clock = FixedClock()
    limits = {"daily_cost_usd": Decimal("1.00")}
    first = _loop(limits, _cost_model(Decimal("0.90")), clock=clock).run("hello")
    second = _loop(limits, _cost_model(Decimal("0.90")), clock=clock).run("hello")
    third_model = _cost_model(Decimal("0.90"))
    third = _loop(limits, third_model, clock=clock).run("hello again")

    assert first.state is SessionState.DONE
    assert second.state is SessionState.STOPPED
    assert second.reason is StopReason.DAILY_COST_EXHAUSTED
    assert third.state is SessionState.STOPPED
    assert third.reason is StopReason.DAILY_COST_EXHAUSTED
    assert third.answer is None
    assert third_model.requests == []
    assert third.daily_usage.cost_usd == Decimal("1.80")


def test_a_session_that_starts_after_the_day_was_spent_never_calls_the_model() -> None:
    clock = FixedClock()
    limits = {"daily_cost_usd": Decimal("1.00")}
    _loop(limits, _cost_model(Decimal("1.00")), clock=clock).run("hello")

    model = _cost_model(Decimal("0.45"))
    refused = _loop(limits, model, clock=clock).run("hello again")

    assert refused.state is SessionState.STOPPED
    assert refused.reason is StopReason.DAILY_COST_EXHAUSTED
    assert model.requests == []


def test_the_daily_ledger_is_keyed_by_agent_and_utc_date() -> None:
    clock = FixedClock()
    limits = {"daily_cost_usd": Decimal("0.10")}
    first = _loop(limits, _cost_model(Decimal("0.05")), clock=clock, agent="alice").run("hello")
    assert first.state is SessionState.DONE

    other_agent = _loop(limits, _cost_model(Decimal("0.05")), clock=clock, agent="bob").run("hello")
    assert other_agent.state is SessionState.DONE
    assert other_agent.daily_usage.cost_usd == Decimal("0.05")

    clock.advance(24 * 60 * 60)
    next_day = _loop(limits, _cost_model(Decimal("0.05")), clock=clock, agent="alice").run("hello")
    assert next_day.state is SessionState.DONE
    assert next_day.daily_usage.cost_usd == Decimal("0.05")


def test_a_turn_that_exactly_reaches_the_cap_still_delivers_its_answer() -> None:
    outcome = _loop({"session_cost_usd": Decimal("0.25")}, _cost_model(Decimal("0.25"))).run(
        "hello"
    )

    assert outcome.state is SessionState.DONE
    assert outcome.reason is StopReason.COMPLETED
    assert outcome.answer is not None
    assert outcome.usage.cost_usd == Decimal("0.25")


def test_a_turn_that_crosses_the_session_cap_stops_instead_of_completing() -> None:
    outcome = _loop({"session_cost_usd": Decimal("0.01")}, _cost_model(Decimal("5.00"))).run(
        "hello"
    )

    assert outcome.state is SessionState.STOPPED
    assert outcome.reason is StopReason.SESSION_COST_EXHAUSTED
    assert outcome.answer is None
    assert outcome.usage.cost_usd == Decimal("5.00")


def test_a_turn_that_crosses_the_daily_cap_stops_instead_of_completing() -> None:
    outcome = _loop(
        {"session_cost_usd": Decimal("10"), "daily_cost_usd": Decimal("0.01")},
        _cost_model(Decimal("5.00")),
    ).run("hello")

    assert outcome.state is SessionState.STOPPED
    assert outcome.reason is StopReason.DAILY_COST_EXHAUSTED
    assert outcome.answer is None


def test_a_turn_that_crosses_the_token_cap_stops_instead_of_completing() -> None:
    model = FakeModel(
        [
            ModelTurn(
                answer=Answer(summary="nothing to report"),
                usage=ModelUsage(prompt_tokens=60, completion_tokens=60),
            )
        ]
    )
    outcome = _loop({"session_tokens": 100}, model).run("hello")

    assert outcome.state is SessionState.STOPPED
    assert outcome.reason is StopReason.SESSION_TOKENS_EXHAUSTED
    assert outcome.answer is None


def test_a_completed_session_carries_its_spend_into_the_daily_ledger() -> None:
    clock = FixedClock()
    limits = make_limits()
    daily = DailyLedger(clock=clock)
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
