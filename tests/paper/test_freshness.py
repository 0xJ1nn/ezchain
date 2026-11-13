"""A stale source fails closed and raises one alert per transition (EZ-210).

Unknown is never zero and an old price is never current: a read past its source class's
budget returns no price and names the stable reason code that refused it. These tests pin
the boundaries (inside the budget, past it, dated ahead, never observed), the per-class
budgets, and the alert de-duplication an operator relies on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from ezchain_core import (
    FreshnessPolicy,
    SourceFreshness,
    SourceRef,
    stale_reason,
)
from ezchain_core.freshness import DEFAULT_MAX_AGE_BY_CLASS, DEFAULT_MAX_AGE_SECONDS

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
PRICE = Decimal("2000")


def source(seconds_ago: float, *, name: str = "dexscreener") -> SourceRef:
    return SourceRef(name, "https://dexscreener.com", NOW - timedelta(seconds=seconds_ago))


def test_an_observation_inside_the_budget_is_fresh() -> None:
    verdict = FreshnessPolicy().classify(source(59.5), now=NOW)
    assert verdict.status == "fresh"
    assert verdict.usable is True
    assert verdict.reason is None
    assert verdict.age_seconds == pytest.approx(59.5)


def test_an_observation_past_the_budget_is_stale_and_prices_nothing() -> None:
    policy = FreshnessPolicy()
    verdict = policy.classify(source(60.5), now=NOW)
    assert verdict.status == "stale"
    assert verdict.usable is False
    assert verdict.reason == "SNAPSHOT_STALE"
    assert policy.price(PRICE, source(60.5), now=NOW) is None


def test_a_fresh_observation_prices() -> None:
    assert FreshnessPolicy().price(PRICE, source(1), now=NOW) == PRICE


def test_a_future_dated_observation_is_not_fresh() -> None:
    """A clamped age cannot separate fresh from future, so the policy reads the skew."""
    verdict = FreshnessPolicy().classify(source(-30), now=NOW)
    assert verdict.status == "future"
    assert verdict.usable is False
    assert verdict.age_seconds is None


def test_no_observation_is_unknown_not_fresh() -> None:
    verdict = FreshnessPolicy().classify(None, now=NOW)
    assert verdict.status == "unknown"
    assert verdict.usable is False
    assert verdict.age_seconds is None


@pytest.mark.parametrize("price", [None, Decimal(0), Decimal("-1")])
def test_a_missing_or_non_positive_price_never_prices(price: Decimal | None) -> None:
    assert FreshnessPolicy().price(price, source(0), now=NOW) is None


def test_budgets_are_per_source_class() -> None:
    policy = FreshnessPolicy(max_age_by_class={"mark": 30.0, "candle": 600.0})
    aged = source(120)
    assert policy.classify(aged, source_class="mark", now=NOW).status == "stale"
    assert policy.classify(aged, source_class="candle", now=NOW).status == "fresh"
    assert policy.budget("mark") == 30.0
    assert policy.budget("candle") == 600.0


def test_an_unlisted_class_falls_back_to_the_default_budget() -> None:
    policy = FreshnessPolicy(max_age_by_class={"mark": 30.0}, default_max_age_seconds=90.0)
    assert policy.budget("feed") == 90.0
    assert policy.budget("mark") == 30.0


def test_quote_and_mark_refusals_name_different_reason_codes() -> None:
    assert stale_reason("quote") == "QUOTE_STALE"
    assert stale_reason("mark") == "SNAPSHOT_STALE"


def test_the_defaults_are_bounded_and_never_zero() -> None:
    assert DEFAULT_MAX_AGE_SECONDS > 0
    assert all(budget > 0 for budget in DEFAULT_MAX_AGE_BY_CLASS.values())
    assert FreshnessPolicy().budget("mark") == DEFAULT_MAX_AGE_SECONDS


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_a_non_positive_budget_is_rejected(bad: float) -> None:
    with pytest.raises(ValueError):
        FreshnessPolicy(max_age_by_class={"mark": bad})
    with pytest.raises(ValueError):
        FreshnessPolicy(default_max_age_seconds=bad)


def test_one_alert_per_transition_not_one_per_read() -> None:
    tracker = SourceFreshness()
    assert tracker.observe("mark:blast", source(0), now=NOW) is None
    first = tracker.observe("mark:blast", source(61), now=NOW)
    assert first is not None
    assert first.type == "provider_stale"
    assert first.entity == "mark:blast"
    assert first.dedupe_key == "mark:blast:provider_stale"
    assert first.age_seconds == pytest.approx(61)
    # The same outage read again does not raise a second alert.
    assert tracker.observe("mark:blast", source(62), now=NOW) is None
    assert tracker.observe("mark:blast", source(120), now=NOW) is None
    assert len(tracker.alerts()) == 1


def test_recovery_closes_the_outage_so_a_later_one_raises_again() -> None:
    tracker = SourceFreshness()
    assert tracker.observe("mark:blast", source(0), now=NOW) is None
    assert tracker.observe("mark:blast", source(61), now=NOW) is not None
    assert tracker.is_stale("mark:blast") is True
    # A fresh observation is the recovery.
    assert tracker.observe("mark:blast", source(1), now=NOW) is None
    assert tracker.is_stale("mark:blast") is False
    assert tracker.observe("mark:blast", source(90), now=NOW) is not None
    assert len(tracker.alerts()) == 2


def test_a_source_never_observed_alerts_as_stale() -> None:
    tracker = SourceFreshness()
    alert = tracker.observe("feed:pumpfun", None, source_class="feed", now=NOW)
    assert alert is not None
    assert alert.age_seconds is None
    assert "no observation" in alert.message


def test_a_future_dated_source_alerts_and_says_why() -> None:
    tracker = SourceFreshness()
    alert = tracker.observe("mark:blast", source(-30), now=NOW)
    assert alert is not None
    assert "ahead of the clock" in alert.message


def test_drain_hands_the_alerts_over_once() -> None:
    tracker = SourceFreshness()
    tracker.observe("mark:blast", source(61), now=NOW)
    drained = tracker.drain()
    assert len(drained) == 1
    assert tracker.drain() == ()
    assert drained[0].to_dict()["type"] == "provider_stale"


def test_two_sources_track_independently() -> None:
    tracker = SourceFreshness()
    tracker.observe("mark:blast", source(61), now=NOW)
    assert tracker.observe("mark:base", source(0), now=NOW) is None
    assert tracker.is_stale("mark:blast") is True
    assert tracker.is_stale("mark:base") is False
