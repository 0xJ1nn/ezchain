"""Golden vectors for the pure whole-profile protection kernel.

One profile can carry a stop loss, a trailing profit and a take profit at once;
``evaluate`` decides from caller-supplied prices only. Every case here is pure:
no market read, clock, ledger, order or database is touched.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from ezchain_core.protection import (
    ProtectionProfile,
    ProtectionValidationError,
    evaluate,
    validate_profile,
)


def make_profile(**thresholds: Any) -> ProtectionProfile:
    values: dict[str, Any] = {
        "stop_loss_pct": None,
        "trailing_pct": None,
        "take_profit_pct": None,
    }
    values.update(thresholds)
    return ProtectionProfile(
        id="profile-1",
        name="guarded",
        chain=None,
        kind="manual",
        rules=(),
        created_at=None,
        updated_at=None,
        **values,
    )


def test_hold_when_mark_is_inside_every_band() -> None:
    decision = evaluate(
        make_profile(
            stop_loss_pct=Decimal(8), trailing_pct=Decimal(5), take_profit_pct=Decimal(20)
        ),
        Decimal(100),
        Decimal(105),
        Decimal(100),
    )
    assert decision.action == "hold"
    assert decision.trigger_price_usd is None
    assert decision.peak_usd == Decimal(105)
    assert decision.inputs["stop_trigger_usd"] == str(Decimal("92.00"))
    assert decision.inputs["trailing_trigger_usd"] == str(Decimal("99.75"))
    assert decision.inputs["take_profit_trigger_usd"] == str(Decimal("120.0"))


def test_stop_exactly_at_trigger_exits() -> None:
    decision = evaluate(make_profile(stop_loss_pct=Decimal(8)), Decimal(100), None, Decimal(92))
    assert decision.action == "exit_stop"
    assert decision.trigger_price_usd == Decimal(92)


def test_stop_one_tick_above_trigger_holds() -> None:
    decision = evaluate(
        make_profile(stop_loss_pct=Decimal(8)), Decimal(100), None, Decimal("92.01")
    )
    assert decision.action == "hold"
    assert decision.trigger_price_usd is None


def test_stop_one_tick_below_trigger_exits() -> None:
    decision = evaluate(
        make_profile(stop_loss_pct=Decimal(8)), Decimal(100), None, Decimal("91.99")
    )
    assert decision.action == "exit_stop"
    assert decision.trigger_price_usd == Decimal(92)


def test_stop_threshold_uses_exact_decimal_arithmetic() -> None:
    entry, pct = Decimal("100.125"), Decimal("8.5")
    decision = evaluate(make_profile(stop_loss_pct=pct), entry, None, Decimal("91.614375"))
    assert decision.trigger_price_usd == Decimal("91.614375")
    assert decision.action == "exit_stop"


def test_trailing_exactly_at_trigger_exits() -> None:
    decision = evaluate(
        make_profile(trailing_pct=Decimal(5)), Decimal(100), Decimal(110), Decimal("104.5")
    )
    assert decision.action == "exit_trailing"
    assert decision.trigger_price_usd == Decimal("104.50")


def test_trailing_one_tick_above_trigger_holds() -> None:
    decision = evaluate(
        make_profile(trailing_pct=Decimal(5)), Decimal(100), Decimal(110), Decimal("104.51")
    )
    assert decision.action == "hold"


def test_trailing_one_tick_below_trigger_exits() -> None:
    decision = evaluate(
        make_profile(trailing_pct=Decimal(5)), Decimal(100), Decimal(110), Decimal("104.49")
    )
    assert decision.action == "exit_trailing"


def test_trailing_ratchets_up_when_current_exceeds_peak() -> None:
    profile = make_profile(trailing_pct=Decimal(5))
    rising = evaluate(profile, Decimal(100), Decimal(105), Decimal(110))
    assert rising.action == "hold"
    assert rising.peak_usd == Decimal(110)
    assert rising.inputs["trailing_trigger_usd"] == str(Decimal("104.50"))
    # Feeding the returned peak forward keeps the higher trigger on the next tick.
    falling = evaluate(profile, Decimal(100), rising.peak_usd, Decimal("104.5"))
    assert falling.action == "exit_trailing"
    assert falling.trigger_price_usd == Decimal("104.50")
    assert str(falling.trigger_price_usd) == rising.inputs["trailing_trigger_usd"]


def test_a_lower_mark_never_lowers_the_trigger() -> None:
    profile = make_profile(trailing_pct=Decimal(5))
    top = evaluate(profile, Decimal(100), Decimal(110), Decimal(110))
    lower = evaluate(profile, Decimal(100), top.peak_usd, Decimal(105))
    assert lower.action == "hold"
    assert lower.inputs["trailing_trigger_usd"] == top.inputs["trailing_trigger_usd"]
    assert lower.peak_usd == Decimal(110)


def test_peak_at_or_below_entry_never_arms_trailing() -> None:
    profile = make_profile(trailing_pct=Decimal(5))
    decision = evaluate(profile, Decimal(100), Decimal(100), Decimal(80))
    assert decision.action == "hold"
    assert "trailing_trigger_usd" not in decision.inputs
    underwater = evaluate(profile, Decimal(100), Decimal(95), Decimal(90))
    assert underwater.action == "hold"
    assert "trailing_trigger_usd" not in underwater.inputs


def test_first_tick_without_peak_uses_current_as_peak() -> None:
    decision = evaluate(make_profile(trailing_pct=Decimal(5)), Decimal(100), None, Decimal(120))
    assert decision.action == "hold"
    assert decision.peak_usd == Decimal(120)
    assert decision.inputs["peak_usd"] == str(Decimal(100))


def test_stop_loss_wins_over_trailing_when_both_are_due() -> None:
    decision = evaluate(
        make_profile(stop_loss_pct=Decimal(8), trailing_pct=Decimal(20)),
        Decimal(100),
        Decimal(110),
        Decimal(88),
    )
    assert decision.action == "exit_stop"
    assert decision.trigger_price_usd == Decimal(92)


def test_trailing_wins_over_take_profit_when_both_are_due() -> None:
    decision = evaluate(
        make_profile(trailing_pct=Decimal(30), take_profit_pct=Decimal(20)),
        Decimal(100),
        Decimal(140),
        Decimal(98),
    )
    assert decision.action == "exit_trailing"
    assert decision.trigger_price_usd == Decimal(98)


def test_take_profit_exactly_at_trigger_exits() -> None:
    decision = evaluate(make_profile(take_profit_pct=Decimal(20)), Decimal(100), None, Decimal(120))
    assert decision.action == "exit_take_profit"
    assert decision.trigger_price_usd == Decimal(120)


def test_take_profit_one_tick_below_trigger_holds() -> None:
    decision = evaluate(
        make_profile(take_profit_pct=Decimal(20)), Decimal(100), None, Decimal("119.99")
    )
    assert decision.action == "hold"


def test_take_profit_one_tick_above_trigger_exits() -> None:
    decision = evaluate(
        make_profile(take_profit_pct=Decimal(20)), Decimal(100), None, Decimal("120.01")
    )
    assert decision.action == "exit_take_profit"


def test_stop_only_profile_ignores_absent_thresholds() -> None:
    decision = evaluate(make_profile(stop_loss_pct=Decimal(8)), Decimal(100), None, Decimal(93))
    assert decision.action == "hold"
    assert "trailing_trigger_usd" not in decision.inputs
    assert "take_profit_trigger_usd" not in decision.inputs


def test_trailing_only_profile_ignores_absent_thresholds() -> None:
    decision = evaluate(
        make_profile(trailing_pct=Decimal(5)), Decimal(100), Decimal(110), Decimal(106)
    )
    assert decision.action == "hold"
    assert "stop_trigger_usd" not in decision.inputs
    assert "take_profit_trigger_usd" not in decision.inputs


def test_take_profit_only_profile_ignores_absent_thresholds() -> None:
    decision = evaluate(
        make_profile(take_profit_pct=Decimal(20)), Decimal(100), Decimal(110), Decimal(99)
    )
    assert decision.action == "hold"
    assert "stop_trigger_usd" not in decision.inputs
    assert "trailing_trigger_usd" not in decision.inputs


def test_empty_profile_is_refused() -> None:
    with pytest.raises(ProtectionValidationError) as caught:
        evaluate(make_profile(), Decimal(100), None, Decimal(100))
    assert caught.value.code == "PROFILE_PARAM_MISSING"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("stop_loss_pct", Decimal(0)),
        ("stop_loss_pct", Decimal(-1)),
        ("stop_loss_pct", Decimal("100.0001")),
        ("trailing_pct", Decimal(0)),
        ("trailing_pct", Decimal(-1)),
        ("trailing_pct", Decimal(100)),
        ("trailing_pct", Decimal("100.1")),
        ("take_profit_pct", Decimal(0)),
        ("take_profit_pct", Decimal(-5)),
        ("stop_loss_pct", 5.5),
        ("trailing_pct", "5"),
        ("take_profit_pct", True),
        ("stop_loss_pct", Decimal("NaN")),
        ("trailing_pct", Decimal("Infinity")),
    ],
)
def test_profile_percentage_refusals(key: str, value: Any) -> None:
    with pytest.raises(ProtectionValidationError) as caught:
        evaluate(make_profile(**{key: value}), Decimal(100), None, Decimal(100))
    assert caught.value.code in {"PROFILE_PARAM_TYPE", "PROFILE_PARAM_RANGE"}


@pytest.mark.parametrize(
    ("entry", "peak", "current"),
    [
        (0, None, Decimal(100)),
        (Decimal("NaN"), None, Decimal(100)),
        (Decimal(100), None, 0),
        (Decimal(100), None, "99"),
        (Decimal(100), Decimal(-1), Decimal(100)),
        (Decimal(100), Decimal("Infinity"), Decimal(100)),
        (5.0, None, Decimal(100)),
    ],
)
def test_price_inputs_are_refused(entry: Any, peak: Any, current: Any) -> None:
    with pytest.raises(ProtectionValidationError) as caught:
        evaluate(make_profile(stop_loss_pct=Decimal(8)), entry, peak, current)
    assert caught.value.code == "PROFILE_PRICE_INVALID"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("stop_loss_pct", "0"),
        ("stop_loss_pct", "101"),
        ("trailing_pct", "100"),
        ("trailing_pct", "-1"),
        ("take_profit_pct", "-2"),
    ],
)
def test_validate_profile_enforces_the_same_bounds(key: str, value: str) -> None:
    params = {key: value}
    with pytest.raises(ProtectionValidationError) as caught:
        validate_profile("guarded", None, "manual", params, None)
    assert caught.value.code == "PROFILE_PARAM_RANGE"


def test_validate_profile_keeps_exact_decimal_strings() -> None:
    stop, trailing, take, rules = validate_profile(
        "guarded", "solana", "manual", {"stop_loss_pct": "8.125", "take_profit_pct": "20"}, None
    )
    assert stop == Decimal("8.125")
    assert trailing is None
    assert take == Decimal(20)
    assert rules == ()


def test_decision_contains_no_float_anywhere() -> None:
    decision = evaluate(
        make_profile(stop_loss_pct=Decimal("8.5"), trailing_pct=Decimal(5)),
        Decimal("100.125"),
        Decimal(110),
        Decimal("91.6"),
    )
    values: list[Any] = [
        decision.action,
        decision.trigger_price_usd,
        decision.peak_usd,
        *decision.inputs.values(),
    ]
    assert not any(isinstance(value, float) for value in values)
    assert all(isinstance(value, (Decimal, str)) for value in values[1:])
