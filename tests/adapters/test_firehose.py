"""Pumpfun-firehose adapter: normalization, staleness gate, snapshot+deltas.

All inputs are the committed trimmed fixture (``firehose_sample.json``) replayed
through the same decode path. No test reaches the network, signs, or trades.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from ezchain_adapters.firehose import (
    DEFAULT_STALE_BUDGET_SECONDS,
    SOURCE_NAME,
    SOURCE_URL,
    FirehoseFeed,
    MarketEvent,
    normalize_firehose_event,
)

SOURCES = {"create", "trade", "complete"}
BASE = 1_760_000_000


def _at(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)


NOW = _at(BASE + 4_000)


def test_source_conventions_match_package() -> None:
    assert SOURCE_NAME == "pumpfun-firehose"
    assert SOURCE_URL == "https://github.com/0xJ1nn/pumpfun-firehose"
    assert DEFAULT_STALE_BUDGET_SECONDS == 60.0


def test_fixture_replays_to_contract_events(load_fixture: Callable[[str], Any]) -> None:
    raw = load_fixture("firehose_sample.json")
    assert isinstance(raw, list) and len(raw) >= 4
    events = [normalize_firehose_event(item) for item in raw]
    assert all(event is not None for event in events)
    assert [event.kind for event in events if event] == [
        "create",
        "trade",
        "trade",
        "complete",
        "trade",
    ]
    mint = raw[0]["mint"]
    for event in events:
        assert isinstance(event, MarketEvent)
        assert event.chain == "solana"
        assert event.mint == mint
        assert event.source.name == SOURCE_NAME
        assert event.source.url == SOURCE_URL
        assert event.source.observed_at == event.observed_at
        assert event.source.observed_at.tzinfo is UTC
        assert event.units == "SOL"
        # Age is computed at read time, not stamped at write time.
        assert event.age_seconds(NOW) == (NOW - event.observed_at).total_seconds()
        assert event.is_stale(DEFAULT_STALE_BUDGET_SECONDS, NOW) == (
            event.age_seconds(NOW) > DEFAULT_STALE_BUDGET_SECONDS
        )
        body = event.to_dict(NOW)
        assert body["source"] == event.source.to_dict()
        assert body["age_seconds"] == event.age_seconds(NOW)
        assert body["observed_at"] == event.observed_at.isoformat()
        assert body["chain"] == "solana"


def test_create_maps_metadata_and_derives_price(
    load_fixture: Callable[[str], Any],
) -> None:
    raw = load_fixture("firehose_sample.json")
    event = normalize_firehose_event(raw[0])
    assert event is not None
    assert event.kind == "create"
    assert event.side is None
    assert event.symbol == "WHACKADON"
    assert event.name == "Whack-A-Don"
    assert event.creator is not None
    assert event.price_sol == Decimal("30.0") / Decimal("1073000000000.0")


def test_trades_map_side_size_and_venue(load_fixture: Callable[[str], Any]) -> None:
    raw = load_fixture("firehose_sample.json")
    buy = normalize_firehose_event(raw[1])
    sell = normalize_firehose_event(raw[2])
    amm = normalize_firehose_event(raw[4])
    assert buy and sell and amm
    assert (buy.side, sell.side) == ("buy", "sell")
    assert buy.price_sol == Decimal("2.793e-08")
    assert buy.size_sol == Decimal("1.5")
    assert buy.size_tokens == Decimal("53694315.0")
    assert sell.size_sol == Decimal("0.5")
    assert buy.venue == "pumpfun"
    assert amm.venue == "pumpswap"
    assert amm.pool == "PoolPDA1111111111111111111111111111111111111111"
    assert amm.size_sol == Decimal("0.75")


def test_complete_has_no_side_or_price(load_fixture: Callable[[str], Any]) -> None:
    raw = load_fixture("firehose_sample.json")
    event = normalize_firehose_event(raw[3])
    assert event is not None
    assert event.kind == "complete"
    assert event.side is None
    assert event.price_sol is None
    assert event.size_sol is None


def test_unknown_timestamp_fails_closed(
    load_fixture: Callable[[str], Any],
) -> None:
    raw = load_fixture("firehose_sample.json")[1]
    assert isinstance(raw, dict)
    # Built per-case so each mutation is isolated; unknown is never zero.
    assert normalize_firehose_event({k: v for k, v in raw.items() if k != "timestamp"}) is None
    assert normalize_firehose_event({**raw, "timestamp": None}) is None
    assert normalize_firehose_event({**raw, "timestamp": 0}) is None
    assert normalize_firehose_event({**raw, "timestamp": -5}) is None
    assert normalize_firehose_event({**raw, "timestamp": True}) is None
    assert normalize_firehose_event({**raw, "timestamp": "1760000000"}) is None
    assert normalize_firehose_event({**raw, "timestamp": 1760000000.5}) is None


def test_malformed_events_are_dropped(
    load_fixture: Callable[[str], Any],
) -> None:
    raw = load_fixture("firehose_sample.json")[1]
    assert isinstance(raw, dict)
    assert normalize_firehose_event(None) is None
    assert normalize_firehose_event("not an event") is None
    assert normalize_firehose_event({**raw, "mint": ""}) is None
    assert normalize_firehose_event({**raw, "kind": "mystery"}) is None
    assert normalize_firehose_event({**raw, "kind": "raw", "event": "FeeEvent"}) is None


def test_unknown_price_stays_none_never_zero(
    load_fixture: Callable[[str], Any],
) -> None:
    raw = load_fixture("firehose_sample.json")[1]
    assert isinstance(raw, dict)
    for bad in (None, "NaN", "Infinity", "oops", 0, -1):
        event = normalize_firehose_event({**raw, "price_sol": bad})
        assert event is not None
        assert event.price_sol is None


def test_stale_feed_degrades_reads_and_alerts_once(
    load_fixture: Callable[[str], Any],
) -> None:
    raw = load_fixture("firehose_sample.json")
    feed = FirehoseFeed()
    event = feed.ingest(raw[1])
    assert event is not None
    fresh = _at(BASE + 60)
    assert feed.fresh_events(fresh)
    assert feed.health(fresh)["stale"] is False

    stale_now = _at(BASE + 60) + timedelta(seconds=DEFAULT_STALE_BUDGET_SECONDS + 1)
    first = feed.health(stale_now)
    assert first["stale"] is True
    assert feed.fresh_events(stale_now) == ()
    snap = feed.snapshot(stale_now)
    assert snap["stale"] is True
    assert snap["alerts"]
    assert snap["alerts"][0]["type"] == "provider_stale"
    assert snap["alerts"][0]["entity"] == SOURCE_NAME
    # Same transition does not spam a second alert.
    second = feed.health(stale_now + timedelta(seconds=1))
    assert len(second["alerts"]) == len(first["alerts"])
    assert len(feed.health(stale_now)["alerts"]) == 1


def test_recovery_clears_stale_without_dropping_history(
    load_fixture: Callable[[str], Any],
) -> None:
    raw = load_fixture("firehose_sample.json")
    feed = FirehoseFeed()
    assert feed.ingest(raw[1]) is not None
    stale_now = _at(BASE) + timedelta(seconds=DEFAULT_STALE_BUDGET_SECONDS + 10)
    assert feed.health(stale_now + timedelta(seconds=200))["stale"] is True
    recovered = feed.ingest(raw[2])
    assert recovered is not None
    now = _at(BASE + 120)
    assert feed.health(now)["stale"] is False
    assert feed.fresh_events(now)
    assert len(feed.snapshot(now)["recent"]) == 2


def test_no_event_without_timestamp_passes_unknown_feed() -> None:
    feed = FirehoseFeed()
    assert feed.fresh_events(_at(BASE)) == ()
    health = feed.health(_at(BASE))
    assert health["last_event_age_seconds"] is None
    assert health["stale"] is True


def test_snapshot_then_deltas_for_api_surface(
    load_fixture: Callable[[str], Any],
) -> None:
    raw = load_fixture("firehose_sample.json")
    feed = FirehoseFeed()
    events = feed.replay([raw[0], raw[1]])
    assert len(events) == 2
    snap = feed.snapshot(_at(BASE + 60))
    assert snap["source"]["name"] == SOURCE_NAME
    assert snap["stale"] is False
    assert len(snap["recent"]) == 2
    assert {row["mint"] for row in snap["coins"]} == {raw[0]["mint"]}


async def test_async_stream_yields_snapshot_then_delta(
    load_fixture: Callable[[str], Any],
) -> None:
    raw = load_fixture("firehose_sample.json")
    feed = FirehoseFeed()
    feed.ingest(raw[0])
    stream = feed.stream(_at(BASE))
    first = await anext(stream)
    assert first["type"] == "snapshot"
    feed.ingest(raw[1])
    second = await anext(stream)
    assert second["type"] == "delta"
    assert second["event"]["kind"] == "trade"
    assert second["event"]["source"]["name"] == SOURCE_NAME
    await stream.aclose()


def test_duplicate_signature_is_counted_not_emitted_twice(
    load_fixture: Callable[[str], Any],
) -> None:
    raw = load_fixture("firehose_sample.json")
    feed = FirehoseFeed()
    assert feed.ingest(raw[1]) is not None
    assert feed.ingest(raw[1]) is None
    health = feed.health(_at(BASE + 60))
    assert health["decoded"] == 1
    assert health["duplicates"] == 1
    assert len(feed.snapshot(_at(BASE + 60))["recent"]) == 1


def test_reconnect_and_reject_counters_are_visible(
    load_fixture: Callable[[str], Any],
) -> None:
    raw = load_fixture("firehose_sample.json")
    feed = FirehoseFeed()
    assert feed.ingest(raw[1]) is not None
    assert feed.ingest({"kind": "trade", "mint": "x"}) is None
    feed.record_reconnect()
    health = feed.health(_at(BASE + 60))
    assert health["decoded"] == 1
    assert health["rejected"] == 1
    assert health["reconnects"] == 1
    assert health["decoded_ratio"] == 0.5


def test_adapter_is_read_only() -> None:
    feed = FirehoseFeed()
    for forbidden in ("sign", "trade", "execute", "order", "buy", "sell"):
        assert not hasattr(feed, forbidden), forbidden
