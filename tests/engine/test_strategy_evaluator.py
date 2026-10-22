"""Pure evaluator tests: each kind, exits, cooldowns and typed validation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from ezchain_core.strategy import (
    StrategyValidationError,
    change_for_lookback,
    evaluate_strategy,
    interval_minutes,
    validate_params,
)
from helpers import make_snapshot, make_spec as spec


def test_momentum_buys_when_lookback_change_crosses_entry() -> None:
    decision = evaluate_strategy(spec("momentum"), make_snapshot(change_h1="5.0"))
    assert decision.action == "buy"
    assert decision.amount_usd == Decimal("100")
    assert decision.reason == "MOMENTUM_ENTRY"
    assert decision.inputs["lookback"] == "1h"
    assert decision.inputs["change_pct"] == "5.0"


def test_momentum_waits_below_entry() -> None:
    decision = evaluate_strategy(spec("momentum"), make_snapshot(change_h1="4.9"))
    assert decision.action == "hold"
    assert decision.reason == "MOMENTUM_WAIT"


def test_momentum_exits_on_change_reversal() -> None:
    decision = evaluate_strategy(
        spec("momentum"),
        make_snapshot(change_h1="-3.5"),
        position_quantity=Decimal("100"),
        position_avg_cost_usd=Decimal("1"),
    )
    assert decision.action == "sell"
    assert decision.reason == "MOMENTUM_EXIT"
    assert decision.amount_usd == Decimal("100")


def test_momentum_take_profit_sells_the_whole_position() -> None:
    decision = evaluate_strategy(
        spec("momentum", {"take_profit_pct": 20, "stop_loss_pct": 8}),
        make_snapshot(price="1.30", change_h1="1"),
        position_quantity=Decimal("100"),
        position_avg_cost_usd=Decimal("1"),
    )
    assert decision.action == "sell"
    assert decision.reason == "TAKE_PROFIT"
    # A partial sell would leave dust that the next tick would sell again.
    assert decision.amount_usd == Decimal("130.00")
    assert decision.inputs["unrealized_pct"] == "30.00"


def test_momentum_stop_loss_wins_over_exit_signal() -> None:
    decision = evaluate_strategy(
        spec("momentum", {"take_profit_pct": 20, "stop_loss_pct": 8}),
        make_snapshot(price="0.90", change_h1="-9"),
        position_quantity=Decimal("100"),
        position_avg_cost_usd=Decimal("1"),
    )
    assert decision.action == "sell"
    assert decision.reason == "STOP_LOSS"
    assert decision.amount_usd == Decimal("90.00")


def test_dip_buys_only_on_a_drop() -> None:
    decision = evaluate_strategy(spec("dip"), make_snapshot(change_h1="-5.0"))
    assert decision.action == "buy"
    assert decision.reason == "DIP_ENTRY"

    waiting = evaluate_strategy(spec("dip"), make_snapshot(change_h1="-4.9"))
    assert waiting.action == "hold"
    assert waiting.reason == "DIP_WAIT"


def test_dip_exits_on_stop_loss() -> None:
    decision = evaluate_strategy(
        spec("dip", {"take_profit_pct": 20, "stop_loss_pct": 8}),
        make_snapshot(price="0.91", change_h1="-10"),
        position_quantity=Decimal("50"),
        position_avg_cost_usd=Decimal("1"),
    )
    assert decision.reason == "STOP_LOSS"
    assert decision.amount_usd == Decimal("45.50")


def test_dca_respects_its_own_interval() -> None:
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    recent = spec("dca", {"interval_minutes": 60}, last_run_at=now - timedelta(minutes=30))
    decision = evaluate_strategy(recent, make_snapshot(), now=now)
    assert decision.reason == "DCA_INTERVAL"
    assert decision.action == "hold"

    due = spec("dca", {"interval_minutes": 60}, last_run_at=now - timedelta(minutes=61))
    assert evaluate_strategy(due, make_snapshot(), now=now).reason == "DCA_BUY"


def test_dca_caps_the_buy_at_the_position_limit() -> None:
    params = {"order_usd": 100, "max_position_usd": 500}
    decision = evaluate_strategy(
        spec("dca", params),
        make_snapshot(price="1"),
        position_quantity=Decimal("480"),
    )
    assert decision.action == "buy"
    assert decision.amount_usd == Decimal("20")

    full = evaluate_strategy(
        spec("dca", params),
        make_snapshot(price="1"),
        position_quantity=Decimal("500"),
    )
    assert full.action == "hold"
    assert full.reason == "MAX_POSITION"


def test_threshold_buys_at_or_below_and_sells_above() -> None:
    params = {"buy_below_usd": 1, "sell_above_usd": 2}
    bought = evaluate_strategy(spec("threshold", params), make_snapshot(price="1"))
    assert bought.action == "buy"
    assert bought.reason == "THRESHOLD_BUY"

    waiting = evaluate_strategy(spec("threshold", params), make_snapshot(price="1.5"))
    assert waiting.reason == "THRESHOLD_WAIT"

    sold = evaluate_strategy(
        spec("threshold", params),
        make_snapshot(price="2"),
        position_quantity=Decimal("10"),
    )
    assert sold.action == "sell"
    assert sold.reason == "THRESHOLD_SELL"
    assert sold.amount_usd == Decimal("20")


def test_paused_never_evaluates_the_market() -> None:
    decision = evaluate_strategy(spec("momentum", status="paused"), make_snapshot())
    assert decision.action == "hold"
    assert decision.reason == "PAUSED"


def test_stale_or_unknown_marks_hold() -> None:
    assert evaluate_strategy(spec("momentum"), make_snapshot(stale=True)).reason == "MARK_STALE"
    assert evaluate_strategy(spec("momentum"), make_snapshot(price=None)).reason == "PRICE_UNKNOWN"


def test_missing_lookback_change_is_not_treated_as_zero() -> None:
    decision = evaluate_strategy(spec("momentum"), make_snapshot(change_h1=None))
    assert decision.action == "hold"
    assert decision.reason == "CHANGE_UNKNOWN"


def test_change_for_lookback_uses_the_requested_window() -> None:
    snapshot = make_snapshot(change_m5="1", change_h1="2", change_h6="3", change_h24="4")
    assert change_for_lookback(snapshot, "5m") == Decimal("1")
    assert change_for_lookback(snapshot, "24h") == Decimal("4")
    assert change_for_lookback(snapshot, "1d") is None


def test_params_are_typed_and_defaulted() -> None:
    params = validate_params("momentum", {})
    assert params.interval_minutes == 60
    assert params.order_usd == Decimal("100")
    assert params.max_position_usd == Decimal("500")
    assert interval_minutes("dca", {"interval_minutes": 15}) == 15


@pytest.mark.parametrize(
    ("kind", "params", "code"),
    [
        ("momentum", {"lookback": "1d"}, "STRATEGY_LOOKBACK_UNSUPPORTED"),
        ("momentum", {"entry_change_pct": 0}, "STRATEGY_PARAM_RANGE"),
        ("momentum", {"exit_change_pct": 3}, "STRATEGY_PARAM_RANGE"),
        ("momentum", {"stop_los_pct": 8}, "STRATEGY_PARAM_UNKNOWN"),
        ("dip", {"order_usd": -1}, "STRATEGY_PARAM_RANGE"),
        ("dca", {"interval_minutes": 0}, "STRATEGY_PARAM_RANGE"),
        ("dca", {"interval_minutes": 1.5}, "STRATEGY_PARAM_RANGE"),
        ("dca", {"max_position_usd": "abc"}, "STRATEGY_PARAM_TYPE"),
        ("threshold", {}, "STRATEGY_PARAM_MISSING"),
        ("threshold", {"buy_below_usd": 2, "sell_above_usd": 2}, "STRATEGY_PARAM_RANGE"),
        ("threshold", {"buy_below_usd": 1, "sell_above_usd": 2, "timeout": 5}, "STRATEGY_PARAM_UNKNOWN"),
    ],
)
def test_invalid_params_are_rejected(kind: str, params: dict[str, object], code: str) -> None:
    with pytest.raises(StrategyValidationError) as exc:
        validate_params(kind, params)  # type: ignore[arg-type]
    assert exc.value.code == code


def test_bool_is_not_a_number() -> None:
    with pytest.raises(StrategyValidationError):
        validate_params("dca", {"order_usd": True})


def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(StrategyValidationError) as exc:
        validate_params("grid", {})  # type: ignore[arg-type]
    assert exc.value.code == "STRATEGY_KIND_UNSUPPORTED"
