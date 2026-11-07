"""Pure protection evaluator: fairness, trailing, take-profit, profile resolution."""

from __future__ import annotations

from decimal import Decimal

import pytest

from ezchain_core.protection import (
    ProtectionValidationError,
    TrailingState,
    evaluate_protection,
    resolve_profile_params,
    validate_protection_params,
)


def _dec(value: str) -> Decimal:
    return Decimal(value)


def test_stop_loss_triggers_on_percent() -> None:
    params = validate_protection_params("stop_loss", {"loss_pct": "8"})
    decision = evaluate_protection(
        kind="stop_loss",
        params=params,
        avg_cost_usd=Decimal("1.00"),
        mark_usd=Decimal("0.92"),
        held_qty=Decimal("100"),
        trailing=TrailingState(),
    )
    assert decision.action == "sell"
    assert decision.reason == "STOP_LOSS"
    assert decision.sell_fraction == Decimal("1")


def test_stop_loss_triggers_on_price() -> None:
    params = validate_protection_params("stop_loss", {"stop_price_usd": "1.84"})
    decision = evaluate_protection(
        kind="stop_loss",
        params=params,
        avg_cost_usd=Decimal("2.00"),
        mark_usd=Decimal("1.80"),
        held_qty=Decimal("50"),
        trailing=TrailingState(),
    )
    assert decision.action == "sell"
    assert decision.reason == "STOP_LOSS"


def test_stop_loss_holds_above_trigger() -> None:
    params = validate_protection_params("stop_loss", {"loss_pct": "8"})
    decision = evaluate_protection(
        kind="stop_loss",
        params=params,
        avg_cost_usd=Decimal("1.00"),
        mark_usd=Decimal("0.95"),
        held_qty=Decimal("100"),
        trailing=TrailingState(),
    )
    assert decision.action == "hold"


def test_take_profit_triggers() -> None:
    params = validate_protection_params("take_profit", {"profit_pct": "20"})
    decision = evaluate_protection(
        kind="take_profit",
        params=params,
        avg_cost_usd=Decimal("1.00"),
        mark_usd=Decimal("1.20"),
        held_qty=Decimal("100"),
        trailing=TrailingState(),
    )
    assert decision.action == "sell"
    assert decision.reason == "TAKE_PROFIT"


def test_trailing_never_above_water_does_not_trigger() -> None:
    """Fairness: a trailing stop that has never activated must not fire."""
    params = validate_protection_params(
        "trailing_stop", {"activation_pct": "10", "giveback_pct": "5"}
    )
    # Price goes straight down; it is never 10% above cost.
    decision = evaluate_protection(
        kind="trailing_stop",
        params=params,
        avg_cost_usd=Decimal("1.00"),
        mark_usd=Decimal("0.50"),
        held_qty=Decimal("100"),
        trailing=TrailingState(peak=None, activated=False),
    )
    assert decision.action == "hold"
    assert decision.reason == "TRAILING_NOT_ACTIVATED"
    assert decision.new_activated is False


def test_trailing_activates_then_giveback_triggers() -> None:
    params = validate_protection_params(
        "trailing_stop", {"activation_pct": "10", "giveback_pct": "5"}
    )
    activated = evaluate_protection(
        kind="trailing_stop",
        params=params,
        avg_cost_usd=Decimal("1.00"),
        mark_usd=Decimal("1.20"),
        held_qty=Decimal("100"),
        trailing=TrailingState(peak=None, activated=False),
    )
    assert activated.action == "hold"
    assert activated.new_activated is True
    assert activated.new_peak == Decimal("1.20")

    triggered = evaluate_protection(
        kind="trailing_stop",
        params=params,
        avg_cost_usd=Decimal("1.00"),
        mark_usd=Decimal("1.10"),
        held_qty=Decimal("100"),
        trailing=TrailingState(peak=activated.new_peak, activated=True),
    )
    assert triggered.action == "sell"
    assert triggered.reason == "TRAILING_STOP"


def test_trailing_by_price() -> None:
    params = validate_protection_params(
        "trailing_stop",
        {"activation_pct": "10", "giveback_price_usd": "0.10"},
    )
    decision = evaluate_protection(
        kind="trailing_stop",
        params=params,
        avg_cost_usd=Decimal("1.00"),
        mark_usd=Decimal("1.09"),
        held_qty=Decimal("100"),
        trailing=TrailingState(peak=Decimal("1.20"), activated=True),
    )
    assert decision.action == "sell"
    assert decision.reason == "TRAILING_STOP"


def test_laddered_take_profit_sells_one_rung_at_a_time() -> None:
    params = validate_protection_params(
        "take_profit",
        {
            "ladder": [
                {"profit_pct": "20", "fraction": "0.5"},
                {"profit_pct": "40", "fraction": "0.5"},
            ]
        },
    )
    first = evaluate_protection(
        kind="take_profit",
        params=params,
        avg_cost_usd=Decimal("1.00"),
        mark_usd=Decimal("1.25"),
        held_qty=Decimal("100"),
        trailing=TrailingState(rung_index=0),
    )
    assert first.action == "sell"
    assert first.reason == "TAKE_PROFIT"
    assert first.sell_fraction == Decimal("0.5")
    assert first.new_rung_index == 1

    second = evaluate_protection(
        kind="take_profit",
        params=params,
        avg_cost_usd=Decimal("1.00"),
        mark_usd=Decimal("1.45"),
        held_qty=Decimal("50"),
        trailing=TrailingState(rung_index=1),
    )
    assert second.action == "sell"
    assert second.sell_fraction == Decimal("0.5")
    assert second.new_rung_index == 2


def test_unknown_mark_fails_closed() -> None:
    params = validate_protection_params("stop_loss", {"loss_pct": "8"})
    decision = evaluate_protection(
        kind="stop_loss",
        params=params,
        avg_cost_usd=Decimal("1.00"),
        mark_usd=None,
        held_qty=Decimal("100"),
        trailing=TrailingState(),
    )
    assert decision.action == "hold"
    assert decision.reason == "PRICE_UNKNOWN"


def test_profile_resolution_prefers_chain_over_global_over_explicit() -> None:
    resolved = resolve_profile_params(
        global_defaults={"loss_pct": "8", "sell_fraction": "1"},
        chain_defaults={"loss_pct": "5"},
        explicit={"sell_fraction": "0.5"},
    )
    assert resolved["loss_pct"] == "5"
    assert resolved["sell_fraction"] == "0.5"

    global_only = resolve_profile_params(
        global_defaults={"loss_pct": "8"},
        chain_defaults=None,
        explicit={},
    )
    assert global_only["loss_pct"] == "8"


def test_validation_rejects_unknown_keys() -> None:
    with pytest.raises(ProtectionValidationError):
        validate_protection_params("stop_loss", {"loss_pct": "8", "bogus": "1"})


def test_validation_requires_trigger_field() -> None:
    with pytest.raises(ProtectionValidationError):
        validate_protection_params("stop_loss", {})
