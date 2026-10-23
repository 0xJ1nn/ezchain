"""The pure trade-size resolver: every boundary is a stable reason, never a default."""

from __future__ import annotations

from decimal import Decimal

import pytest

from ezchain_core import (
    TradeSizeLimits,
    TradeSizePolicy,
    effective_trade_size_limits,
    resolve_trade_size,
    validate_trade_policy,
)


def policy() -> TradeSizePolicy:
    return TradeSizePolicy(
        limits=TradeSizeLimits(
            min_amount_usd=Decimal("10"),
            min_pct_equity=Decimal("0.10"),
            max_amount_usd=Decimal("1000"),
            max_pct_equity=Decimal("5"),
        ),
        profiles={
            "low": TradeSizeLimits(
                Decimal("10"), Decimal("0.10"), Decimal("50"), Decimal("1")
            ),
            "mid": TradeSizeLimits(
                Decimal("10"), Decimal("0.10"), Decimal("150"), Decimal("2")
            ),
            "high": TradeSizeLimits(
                Decimal("10"), Decimal("0.10"), Decimal("500"), Decimal("5")
            ),
        },
        default_profile="mid",
    )


@pytest.mark.parametrize(
    ("profile", "expected"),
    [("low", "50"), ("mid", "150"), ("high", "500")],
)
def test_profiles_resolve_to_their_percentage_cap(profile: str, expected: str) -> None:
    result = resolve_trade_size(
        equity_usd=Decimal("10000"), policy=policy(), profile=profile
    )

    assert result.ok
    assert result.amount_usd == Decimal(expected)
    assert result.profile == profile
    assert result.limits is not None
    assert result.manual is False


def test_default_profile_is_mid_and_absolute_cap_wins() -> None:
    result = resolve_trade_size(equity_usd=Decimal("100000"), policy=policy())

    assert result.ok
    assert result.profile == "mid"
    assert result.amount_usd == Decimal("150")


def test_minimum_percentage_above_the_absolute_cap_is_refused() -> None:
    # At $1M equity the 0.10% floor is $1,000, above the $150 mid cap: the resolver
    # refuses instead of returning a size that violates one of the bounds.
    result = resolve_trade_size(
        equity_usd=Decimal("1000000"), policy=policy(), profile="mid"
    )

    assert not result.ok
    assert result.reason == "SIZE_BELOW_MIN"


def test_percentage_cap_wins_on_a_small_account() -> None:
    result = resolve_trade_size(
        equity_usd=Decimal("1000"), policy=policy(), profile="high"
    )

    assert result.ok
    assert result.amount_usd == Decimal("50")


def test_equity_unknown_is_a_refusal_and_keeps_the_limits_snapshot() -> None:
    result = resolve_trade_size(equity_usd=None, policy=policy(), profile="mid")

    assert not result.ok
    assert result.amount_usd is None
    assert result.reason == "EQUITY_UNKNOWN"
    assert result.profile == "mid"
    assert result.limits is not None
    assert result.limits.max_amount_usd == Decimal("150")


def test_equity_too_small_for_the_minimum_is_below_min() -> None:
    result = resolve_trade_size(equity_usd=Decimal("100"), policy=policy(), profile="mid")

    assert not result.ok
    assert result.reason == "SIZE_BELOW_MIN"


def test_unknown_profile_does_not_fall_back_to_the_default() -> None:
    result = resolve_trade_size(
        equity_usd=Decimal("10000"), policy=policy(), profile="aggressive"
    )

    assert not result.ok
    assert result.reason == "PROFILE_UNKNOWN"
    assert result.amount_usd is None
    assert result.limits is None


def test_manual_amount_is_not_resized() -> None:
    result = resolve_trade_size(
        equity_usd=Decimal("10000"),
        policy=policy(),
        profile="mid",
        manual_amount_usd=Decimal("37.42"),
    )

    assert result.ok
    assert result.amount_usd == Decimal("37.42")
    assert result.manual is True


def test_manual_amount_below_minimum_is_refused() -> None:
    result = resolve_trade_size(
        equity_usd=Decimal("10000"),
        policy=policy(),
        profile="mid",
        manual_amount_usd=Decimal("9.99"),
    )

    assert not result.ok
    assert result.reason == "SIZE_BELOW_MIN"
    assert result.amount_usd is None


def test_manual_amount_above_maximum_is_refused() -> None:
    result = resolve_trade_size(
        equity_usd=Decimal("10000"),
        policy=policy(),
        profile="mid",
        manual_amount_usd=Decimal("150.01"),
    )

    assert not result.ok
    assert result.reason == "SIZE_ABOVE_MAX"
    assert result.amount_usd is None


def test_manual_amount_cannot_bypass_an_unknown_equity() -> None:
    result = resolve_trade_size(
        equity_usd=None,
        policy=policy(),
        profile="mid",
        manual_amount_usd=Decimal("25"),
    )

    assert not result.ok
    assert result.reason == "EQUITY_UNKNOWN"


def test_manual_amount_at_each_boundary_is_accepted() -> None:
    at_min = resolve_trade_size(
        equity_usd=Decimal("10000"),
        policy=policy(),
        profile="mid",
        manual_amount_usd=Decimal("10"),
    )
    at_max = resolve_trade_size(
        equity_usd=Decimal("10000"),
        policy=policy(),
        profile="mid",
        manual_amount_usd=Decimal("150"),
    )

    assert at_min.ok and at_min.amount_usd == Decimal("10")
    assert at_max.ok and at_max.amount_usd == Decimal("150")
    assert isinstance(at_min.amount_usd, Decimal)
    assert isinstance(at_max.amount_usd, Decimal)


def test_automatic_target_is_below_min_when_the_caps_cannot_reach_it() -> None:
    result = resolve_trade_size(
        equity_usd=Decimal("400"), policy=policy(), profile="mid"
    )

    # 2% of $400 is $8, below the $10 absolute and the $0.40 percentage floor.
    assert not result.ok
    assert result.reason == "SIZE_BELOW_MIN"
    assert result.amount_usd is None


def test_profile_is_intersected_with_the_workspace_envelope() -> None:
    wide = TradeSizePolicy(
        limits=TradeSizeLimits(
            min_amount_usd=Decimal("10"),
            min_pct_equity=Decimal("0"),
            max_amount_usd=Decimal("100"),
            max_pct_equity=Decimal("100"),
        ),
        profiles={
            "low": TradeSizeLimits(Decimal("10"), Decimal("0"), Decimal("100"), Decimal("100")),
            "mid": TradeSizeLimits(Decimal("10"), Decimal("0"), Decimal("500"), Decimal("100")),
            "high": TradeSizeLimits(Decimal("10"), Decimal("0"), Decimal("900"), Decimal("100")),
        },
        default_profile="mid",
    )

    effective = effective_trade_size_limits(wide, "mid")
    assert effective is not None
    assert effective.max_amount_usd == Decimal("100")


def test_invalid_limits_are_refused_by_the_resolver_and_validator() -> None:
    broken = TradeSizePolicy(
        limits=TradeSizeLimits(
            min_amount_usd=Decimal("100"),
            min_pct_equity=Decimal("0"),
            max_amount_usd=Decimal("10"),
            max_pct_equity=Decimal("0"),
        ),
        profiles={
            "low": TradeSizeLimits(Decimal("0"), Decimal("0"), Decimal("10"), Decimal("0")),
            "mid": TradeSizeLimits(Decimal("0"), Decimal("0"), Decimal("10"), Decimal("0")),
            "high": TradeSizeLimits(Decimal("0"), Decimal("0"), Decimal("10"), Decimal("0")),
        },
        default_profile="mid",
    )

    assert "MIN_ABOVE_MAX" in validate_trade_policy(broken)
    result = resolve_trade_size(equity_usd=Decimal("10000"), policy=broken)
    assert not result.ok
    assert result.reason == "MIN_ABOVE_MAX"


def test_negative_and_out_of_range_values_are_rejected() -> None:
    bad = TradeSizePolicy(
        limits=TradeSizeLimits(
            min_amount_usd=Decimal("-1"),
            min_pct_equity=Decimal("101"),
            max_amount_usd=Decimal("10"),
            max_pct_equity=Decimal("100"),
        ),
        profiles={
            "low": TradeSizeLimits(Decimal("0"), Decimal("0"), Decimal("10"), Decimal("100")),
            "mid": TradeSizeLimits(Decimal("0"), Decimal("0"), Decimal("10"), Decimal("100")),
            "high": TradeSizeLimits(Decimal("0"), Decimal("0"), Decimal("10"), Decimal("100")),
        },
        default_profile="mid",
    )

    errors = validate_trade_policy(bad)
    assert "AMOUNT_NEGATIVE" in errors
    assert "PCT_OUT_OF_RANGE" in errors


def test_non_finite_values_are_rejected_before_any_comparison() -> None:
    bad = TradeSizePolicy(
        limits=TradeSizeLimits(
            min_amount_usd=Decimal("NaN"),
            min_pct_equity=Decimal("0"),
            max_amount_usd=Decimal("10"),
            max_pct_equity=Decimal("100"),
        ),
        profiles={
            "low": TradeSizeLimits(Decimal("0"), Decimal("0"), Decimal("10"), Decimal("100")),
            "mid": TradeSizeLimits(Decimal("0"), Decimal("0"), Decimal("10"), Decimal("100")),
            "high": TradeSizeLimits(Decimal("0"), Decimal("0"), Decimal("10"), Decimal("100")),
        },
        default_profile="mid",
    )

    assert "NOT_FINITE" in validate_trade_policy(bad)
    result = resolve_trade_size(equity_usd=Decimal("10000"), policy=bad)
    assert not result.ok
    assert result.reason == "NOT_FINITE"


def test_missing_or_extra_profile_names_are_rejected() -> None:
    missing = TradeSizePolicy(
        limits=policy().limits,
        profiles={"low": policy().profiles["low"]},
        default_profile="low",
    )
    assert "PROFILE_UNKNOWN" in validate_trade_policy(missing)

    extra = TradeSizePolicy(
        limits=policy().limits,
        profiles={**policy().profiles, "aggressive": policy().profiles["high"]},
        default_profile="mid",
    )
    assert "PROFILE_UNKNOWN" in validate_trade_policy(extra)


def test_profile_outside_the_envelope_is_rejected() -> None:
    outside = TradeSizePolicy(
        limits=TradeSizeLimits(
            min_amount_usd=Decimal("10"),
            min_pct_equity=Decimal("0.10"),
            max_amount_usd=Decimal("100"),
            max_pct_equity=Decimal("5"),
        ),
        profiles={
            "low": TradeSizeLimits(Decimal("10"), Decimal("0.10"), Decimal("50"), Decimal("1")),
            "mid": TradeSizeLimits(Decimal("10"), Decimal("0.10"), Decimal("150"), Decimal("2")),
            "high": TradeSizeLimits(Decimal("10"), Decimal("0.10"), Decimal("500"), Decimal("5")),
        },
        default_profile="mid",
    )

    assert "PROFILE_OUTSIDE_ENVELOPE" in validate_trade_policy(outside)
