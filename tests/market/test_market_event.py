"""One normalised market observation: schema, serialiser, and read-time freshness.

The contract is chain-neutral data. Nothing in these tests asserts a chain branch, and
the failure paths are explicit: unknown units, unknown schema, zero price and a
write-time staleness stamp are all refused rather than coerced.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from ezchain_core import (
    DEFAULT_STALENESS_SECONDS,
    MARKET_EVENT_SCHEMA_VERSION,
    MarketEvent,
    MarketEventError,
    SourceRef,
)

BASE = 1_760_000_000


def _at(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)


def _source(epoch: int = BASE) -> SourceRef:
    return SourceRef("pumpfun-firehose", "https://example.test/firehose", _at(epoch))


def _event(**overrides: Any) -> MarketEvent:
    values: dict[str, Any] = {
        "chain": "solana",
        "venue": "pumpfun",
        "pair": "Mint1111111111111111111111111111111111111111",
        "price": Decimal("2.5"),
        "size": Decimal("1.25"),
        "direction": "buy",
        "units": "SOL",
        "source": _source(),
        "observed_at": _at(BASE + 1),
    }
    values.update(overrides)
    return MarketEvent(**values)


def test_market_event_has_one_schema_version_and_declared_units() -> None:
    event = _event()
    assert MARKET_EVENT_SCHEMA_VERSION == 1
    assert event.schema_version == MARKET_EVENT_SCHEMA_VERSION
    body = event.to_dict()
    assert body["schema_version"] == MARKET_EVENT_SCHEMA_VERSION
    assert body["units"] == "SOL"

    with pytest.raises(MarketEventError, match="schema version"):
        _event(schema_version=2)
    with pytest.raises(MarketEventError, match="units"):
        _event(units="")
    with pytest.raises(MarketEventError, match="chain"):
        _event(chain="")


def test_market_event_round_trips_through_its_one_serialiser() -> None:
    event = _event()
    body = event.to_dict()
    assert MarketEvent.from_dict(body) == event
    assert body["price"] == "2.5"
    assert body["size"] == "1.25"
    assert body["direction"] == "buy"
    assert body["source"] == event.source.to_dict()
    assert body["source_timestamp"] == event.source.observed_at.isoformat()
    assert body["observed_at"] == event.observed_at.isoformat()


def test_market_event_age_and_staleness_are_read_time_not_write_time() -> None:
    event = _event()
    recorded = event.to_dict()
    # The recording never carries a freshness verdict: it is derived when read.
    assert "age_seconds" not in recorded
    assert "stale" not in recorded
    assert "ingest_lag_seconds" not in recorded

    fresh_now = _at(BASE + 10)
    stale_now = _at(BASE + int(DEFAULT_STALENESS_SECONDS) + 1)
    assert event.age_seconds(fresh_now) == 10.0
    assert event.is_stale(DEFAULT_STALENESS_SECONDS, fresh_now) is False
    assert event.is_stale(DEFAULT_STALENESS_SECONDS, stale_now) is True

    fresh = event.to_dict(fresh_now)
    stale = event.to_dict(stale_now)
    assert fresh["age_seconds"] == 10.0
    assert fresh["stale"] is False
    assert stale["age_seconds"] == event.age_seconds(stale_now)
    assert stale["stale"] is True
    # The same recorded observation yields different ages as the clock advances.
    assert recorded == event.to_dict()


def test_write_time_staleness_is_ignored_on_replay() -> None:
    event = _event()
    tampered = {**event.to_dict(), "age_seconds": 999_999.0, "stale": True}
    replayed = MarketEvent.from_dict(tampered)
    assert replayed == event
    assert replayed.age_seconds(_at(BASE)) == 0.0
    assert replayed.is_stale(DEFAULT_STALENESS_SECONDS, _at(BASE + 30)) is False


def test_unknown_price_size_and_confidence_stay_none_never_zero() -> None:
    event = _event(price=None, size=None, confidence=None)
    body = event.to_dict()
    assert body["price"] is None
    assert body["size"] is None
    assert body["confidence"] is None
    replayed = MarketEvent.from_dict(body)
    assert replayed.price is None
    assert replayed.size is None
    assert replayed.confidence is None

    with pytest.raises(MarketEventError, match="price"):
        _event(price=Decimal("0"))
    with pytest.raises(MarketEventError, match="price"):
        _event(price=Decimal("-1"))
    with pytest.raises(MarketEventError, match="size"):
        _event(size=Decimal("0"))
    with pytest.raises(MarketEventError, match="confidence"):
        _event(confidence=Decimal("1.5"))
    with pytest.raises(MarketEventError, match="Decimal"):
        _event(price=2.5)


def test_direction_is_buy_sell_or_unknown() -> None:
    assert _event(direction="sell").direction == "sell"
    assert _event(direction=None).direction is None
    with pytest.raises(MarketEventError, match="direction"):
        _event(direction="hold")


def test_ingest_lag_separates_source_clock_from_ingest_clock() -> None:
    event = _event(observed_at=_at(BASE + 7))
    assert event.ingest_lag_seconds() == 7.0
    assert event.source.observed_at == _at(BASE)
    assert event.observed_at == _at(BASE + 7)


def test_same_contract_serves_every_chain_without_a_branch() -> None:
    solana = _event()
    base = _event(chain="base", venue="uniswap_v3", units="USDC")
    for event in (solana, base):
        replayed = MarketEvent.from_dict(event.to_dict())
        assert replayed == event
        assert replayed.age_seconds(_at(BASE + 5)) == 5.0


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda body: {**body, "schema_version": 99}, "schema version"),
        (lambda body: {**body, "schema_version": "1"}, "schema version"),
        (lambda body: {**body, "units": ""}, "units"),
        (lambda body: {**body, "pair": ""}, "pair"),
        (lambda body: {**body, "direction": "sideways"}, "direction"),
        (lambda body: {**body, "source": None}, "source"),
        (lambda body: {**body, "observed_at": "not-a-time"}, "observed_at"),
        (lambda body: {**body, "source_timestamp": "2000-01-01T00:00:00+00:00"}, "source"),
        (lambda body: {**body, "price": "NaN"}, "price"),
        (lambda body: {**body, "confidence": "2"}, "confidence"),
    ],
)
def test_from_dict_fails_closed_on_a_bad_market_record(mutate: Any, message: str) -> None:
    body = _event().to_dict()
    with pytest.raises(MarketEventError, match=message):
        MarketEvent.from_dict(mutate(body))


def test_from_dict_rejects_a_non_mapping_market_record() -> None:
    with pytest.raises(MarketEventError):
        MarketEvent.from_dict(["not", "an", "event"])
