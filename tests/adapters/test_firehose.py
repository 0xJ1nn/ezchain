"""Pumpfun-firehose adapter: normalization, staleness gate, snapshot+deltas.

All inputs are the committed trimmed fixture (``firehose_sample.json``) replayed
through the same decode path. The fixture carries one sanitised record of real
traffic behind a provenance header; every test reads the events, never the header.
No test reaches the network, signs, or trades.

The adapter emits the one core ``MarketEvent``; ``FirehoseEvent`` only adds this
wire's own extras on top of it, so these tests assert the contract's field names
and its serialiser, not an adapter-local shape.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import ezchain_core
from ezchain_adapters.firehose import (
    DEFAULT_STALE_BUDGET_SECONDS,
    PINNED_VERSION,
    SOURCE_NAME,
    SOURCE_URL,
    FirehoseEvent,
    FirehoseFeed,
    MarketEvent,
    StaleFeed,
    normalize_firehose_event,
)

BASE = 1_760_000_000
FIXTURE = Path(__file__).parent / "fixtures" / "firehose_sample.json"
SENTINEL = re.compile(r"^[A-Za-z]+1{5,}$")


def _at(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)


# Later than every record in the fixture, including the captured one: an observation dated
# ahead of the read clock is a clock fault, and age is floored at zero by the contract.
NOW = _at(1_800_000_000)


def test_source_conventions_match_package() -> None:
    assert SOURCE_NAME == "pumpfun-firehose"
    assert SOURCE_URL == "https://github.com/0xJ1nn/pumpfun-firehose"
    assert DEFAULT_STALE_BUDGET_SECONDS == 60.0
    # The adapter's provenance constant names the tag the ingest extra pins.
    assert PINNED_VERSION == "0.5.0"


def test_the_recorded_fixture_declares_its_provenance_and_is_sanitised() -> None:
    """The committed sample is real traffic, and it says so and is sanitised."""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    meta = raw["_fixture"]
    assert meta["kind"] == "recorded"
    assert meta["captured_at"] == "2026-11-14"
    assert "v0.5.0" in meta["source"]
    # The identifiers a capture carries are replaced; the amounts are the recorded ones.
    assert set(meta["sanitised"]) == {"signature", "mint", "user", "creator"}
    events = raw["events"]
    assert isinstance(events, list) and 4 <= len(events) <= 12
    for event in events:
        # Every record, not only the captured one: the claim covers the whole file.
        # A create carries no ``user`` and a complete carries no ``creator``, so the
        # assertion covers the fields a record actually has.
        assert SENTINEL.match(event["signature"]), event["signature"]
        assert SENTINEL.match(event["mint"]), event["mint"]
        for field in ("user", "creator"):
            if field in event:
                assert SENTINEL.match(event[field]), (field, event[field])


def test_the_recorded_sample_replays_through_the_same_decode_path(
    firehose_sample: list[dict[str, Any]],
) -> None:
    """The recorded record decodes with the adapter the live wire uses, unchanged."""
    event = normalize_firehose_event(firehose_sample[-1])
    assert event is not None
    assert event.kind == "trade"
    assert event.direction == "sell"
    assert event.venue == "pumpfun"
    # The recorded numbers survive normalisation; nothing is invented to fill a gap.
    assert event.price == Decimal("2.3066231e-08")
    assert event.size == Decimal("0.010407149")
    assert event.timestamp == 1_794_672_495
    assert event.observed_at == _at(1_794_672_495)
    assert event.slot is None


def test_fixture_replays_to_contract_events(firehose_sample: list[dict[str, Any]]) -> None:
    raw = firehose_sample
    assert len(raw) >= 4
    events = [normalize_firehose_event(item) for item in raw]
    assert all(event is not None for event in events)
    assert [event.kind for event in events if event] == [
        "create",
        "trade",
        "trade",
        "complete",
        "trade",
        "trade",
    ]

    for index, event in enumerate(events):
        assert isinstance(event, FirehoseEvent)
        # The observation is the one core contract, not an adapter-local type.
        assert isinstance(event.event, MarketEvent)
        assert event.chain == "solana"
        # Each event keeps its own mint; only the trailing capture is a different token.
        assert event.pair == raw[index]["mint"]
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
        # One serialiser: the wire body is the contract's body plus extras.
        contract_body = event.event.to_dict(NOW)
        assert {key: body[key] for key in contract_body} == contract_body
        assert body["kind"] == event.kind


def test_the_contract_itself_replays_and_is_the_one_market_event() -> None:
    """The adapter exports the core type; nothing shadows it with a second one."""
    assert MarketEvent is ezchain_core.MarketEvent
    raw = {
        "kind": "trade",
        "mint": "Mint1111111111111111111111111111111111111",
        "timestamp": BASE,
        "is_buy": True,
        "sol": 1,
        "price_sol": 2,
    }
    event = normalize_firehose_event(raw)
    assert event is not None
    # The contract round-trips on its own serialiser, and freshness is derived.
    contract_body = event.event.to_dict(NOW)
    assert MarketEvent.from_dict(contract_body) == event.event
    assert "age_seconds" not in event.event.to_dict()
    assert "stale" not in event.event.to_dict()


def test_create_maps_metadata_and_derives_price(
    firehose_sample: list[dict[str, Any]],
) -> None:
    event = normalize_firehose_event(firehose_sample[0])
    assert event is not None
    assert event.kind == "create"
    assert event.direction is None
    assert event.symbol == "WHACKADON"
    assert event.name == "Whack-A-Don"
    assert event.creator is not None
    assert event.price == Decimal("30.0") / Decimal("1073000000000.0")


def test_trades_map_side_size_and_venue(firehose_sample: list[dict[str, Any]]) -> None:
    raw = firehose_sample
    buy = normalize_firehose_event(raw[1])
    sell = normalize_firehose_event(raw[2])
    amm = normalize_firehose_event(raw[4])
    assert buy and sell and amm
    assert (buy.direction, sell.direction) == ("buy", "sell")
    assert buy.price == Decimal("2.793e-08")
    assert buy.size == Decimal("1.5")
    assert buy.size_tokens == Decimal("53694315.0")
    assert sell.size == Decimal("0.5")
    assert buy.venue == "pumpfun"
    assert amm.venue == "pumpswap"
    assert amm.pool == "PoolPDA1111111111111111111111111111111111111111"
    assert amm.size == Decimal("0.75")


def test_complete_has_no_side_or_price(firehose_sample: list[dict[str, Any]]) -> None:
    event = normalize_firehose_event(firehose_sample[3])
    assert event is not None
    assert event.kind == "complete"
    assert event.direction is None
    assert event.price is None
    assert event.size is None


def test_unknown_timestamp_fails_closed(
    firehose_sample: list[dict[str, Any]],
) -> None:
    raw = firehose_sample[1]
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
    firehose_sample: list[dict[str, Any]],
) -> None:
    raw = firehose_sample[1]
    assert isinstance(raw, dict)
    assert normalize_firehose_event(None) is None
    assert normalize_firehose_event("not an event") is None
    assert normalize_firehose_event({**raw, "mint": ""}) is None
    assert normalize_firehose_event({**raw, "kind": "mystery"}) is None
    assert normalize_firehose_event({**raw, "kind": "raw", "event": "FeeEvent"}) is None


def test_unknown_price_stays_none_never_zero(
    firehose_sample: list[dict[str, Any]],
) -> None:
    raw = firehose_sample[1]
    assert isinstance(raw, dict)
    for bad in (None, "NaN", "Infinity", "oops", 0, -1):
        event = normalize_firehose_event({**raw, "price_sol": bad})
        assert event is not None
        assert event.price is None


def test_stale_feed_degrades_reads_and_alerts_once(
    firehose_sample: list[dict[str, Any]],
) -> None:
    feed = FirehoseFeed()
    assert feed.ingest(firehose_sample[1]) is not None
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


def test_an_entry_is_refused_while_the_stream_is_stale(
    firehose_sample: list[dict[str, Any]],
) -> None:
    """A gap is untradeable: the entry read raises instead of returning a stale value."""
    feed = FirehoseFeed()
    assert feed.ingest(firehose_sample[1]) is not None  # observed at BASE + 60
    assert feed.ingest(firehose_sample[2]) is not None  # observed at BASE + 120

    stale_now = _at(BASE + 120 + DEFAULT_STALE_BUDGET_SECONDS + 1)
    with pytest.raises(StaleFeed) as refused:
        feed.entries(stale_now)

    assert refused.value.stale is True
    assert refused.value.age_seconds == pytest.approx(DEFAULT_STALE_BUDGET_SECONDS + 1)
    assert refused.value.stale_budget_seconds == DEFAULT_STALE_BUDGET_SECONDS
    # A refusal drains nothing: the observations stay buffered for a live reader.
    assert feed.census()["depth"] == 2
    queued = feed.entries(_at(BASE + 120))
    assert [entry.direction for entry in queued] == ["buy", "sell"]
    assert feed.census()["depth"] == 0


def test_no_entry_at_all_before_the_first_event_is_refused() -> None:
    feed = FirehoseFeed()
    with pytest.raises(StaleFeed) as refused:
        feed.entries(_at(BASE))
    assert refused.value.stale is True
    assert refused.value.age_seconds is None
    assert refused.value.to_dict()["type"] == "provider_stale"


def test_the_queue_drops_the_oldest_under_pressure_and_counts_it(
    firehose_sample: list[dict[str, Any]],
) -> None:
    """Pressure costs the stale end of the backlog, never the reader's progress."""
    feed = FirehoseFeed(queue_size=2)
    for index in range(3):
        assert feed.ingest({**firehose_sample[1], "signature": f"pressure{index}"}) is not None

    census = feed.census()
    assert census["maxsize"] == 2
    assert census["depth"] == 2
    assert census["enqueued"] == 3
    assert census["dropped"] == 1

    # The newest two survive; the dropped one is the oldest.
    queued = feed.entries(_at(BASE + 60))
    assert [entry.signature for entry in queued] == ["pressure1", "pressure2"]
    assert feed.health(_at(BASE + 60))["dropped"] == 1


def test_the_census_never_blocks_the_reader(firehose_sample: list[dict[str, Any]]) -> None:
    """Census is synchronous: reading it from inside a running loop returns a value."""
    feed = FirehoseFeed(queue_size=1)
    assert feed.ingest(firehose_sample[1]) is not None
    assert feed.ingest(firehose_sample[2]) is not None

    async def read_inside_a_running_loop() -> dict[str, Any]:
        # If census awaited anything it would need the loop to keep turning, and a
        # coroutine awaited here would be the reader's own turn: it must return now.
        return feed.census()

    census = asyncio.run(read_inside_a_running_loop())

    assert census == {"depth": 1, "maxsize": 1, "enqueued": 2, "dropped": 1}
    # The counters are fully materialised at call time, both on the queue and in health.
    health = feed.health(_at(BASE + 60))
    assert type(census) is dict
    assert type(health) is dict
    assert not inspect.iscoroutine(census)
    assert health["queue"]["dropped"] == 1


def test_health_reports_age_reconnects_drops_and_the_decode_ratio(
    firehose_sample: list[dict[str, Any]],
) -> None:
    feed = FirehoseFeed(queue_size=1)
    assert feed.ingest(firehose_sample[1]) is not None
    assert feed.ingest({"kind": "trade", "mint": "x"}) is None
    feed.record_reconnect()
    assert feed.ingest(firehose_sample[2]) is not None  # drops the first, counts one

    health = feed.health(_at(BASE + 180))

    assert health["last_event_age_seconds"] == pytest.approx(60.0)
    assert health["last_event_at"] == _at(BASE + 120).isoformat()
    assert health["reconnects"] == 1
    assert health["dropped"] == 1
    assert (health["decoded"], health["rejected"]) == (2, 1)
    assert health["decoded_ratio"] == pytest.approx(2 / 3)
    assert health["rejected_ratio"] == pytest.approx(1 / 3)
    assert health["queue"] == {"depth": 1, "maxsize": 1, "enqueued": 2, "dropped": 1}


def test_an_unobserved_feed_reports_unknown_ratios_not_a_perfect_score() -> None:
    feed = FirehoseFeed()
    health = feed.health(_at(BASE))
    assert health["last_event_age_seconds"] is None
    assert health["last_event_at"] is None
    assert health["decoded_ratio"] is None
    assert health["rejected_ratio"] is None


def test_recovery_clears_stale_without_dropping_history(
    firehose_sample: list[dict[str, Any]],
) -> None:
    raw = firehose_sample
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


def _gap_after(firehose_sample: list[dict[str, Any]], seconds: int) -> dict[str, Any]:
    """The captured record's shape, ``seconds`` past the buy at index 1 on the chain clock."""
    return {
        **firehose_sample[-1],
        "signature": "PostGapSig1111111111111111111111111111111111",
        "timestamp": int(firehose_sample[1]["timestamp"]) + seconds,
    }


def test_after_a_gap_one_fresh_event_does_not_revive_the_pre_gap_backlog(
    firehose_sample: list[dict[str, Any]],
) -> None:
    """The reproduced failure: 1800 s quiet, one fresh event, and the old entry served.

    The newest observation is fresh, so the feed is live and the gate passes. That
    says nothing about the observation buffered before the gap, and the entry read
    must refuse it on its own age, count it, and serve only what it can defend.
    """
    raw = firehose_sample
    feed = FirehoseFeed()
    before_gap = feed.ingest(raw[1])
    assert before_gap is not None

    fresh = feed.ingest(_gap_after(raw, 1800))
    assert fresh is not None
    now = fresh.observed_at

    assert feed.health(now)["stale"] is False
    entries = feed.entries(now)

    assert [entry.signature for entry in entries] == [fresh.signature]
    assert before_gap.signature not in {entry.signature for entry in entries}
    health = feed.health(now)
    assert health["refused_stale"] == 1
    # The refusal is on the item's own age, not a blanket buffer flush.
    assert before_gap.age_seconds(now) == 1800.0
    assert feed.census()["depth"] == 0


def test_a_display_row_carries_its_own_age_mid_gap(
    firehose_sample: list[dict[str, Any]],
) -> None:
    """The snapshot is a display surface: the row is shown, and it is shown as old."""
    raw = firehose_sample
    feed = FirehoseFeed()
    assert feed.ingest(raw[1]) is not None
    mid_gap = _at(BASE + 60) + timedelta(seconds=1800)

    snap = feed.snapshot(mid_gap)

    assert snap["stale"] is True
    (row,) = snap["recent"]
    assert row["age_seconds"] == 1800.0
    assert row["stale"] is True
    # A row is not an entry: the same read is refused outright.
    with pytest.raises(StaleFeed):
        feed.entries(mid_gap)


def test_a_gap_nobody_read_is_still_one_alert(
    firehose_sample: list[dict[str, Any]],
) -> None:
    """Ingest observes the transition, so silence during the gap does not lose the alert."""
    raw = firehose_sample
    feed = FirehoseFeed()
    assert feed.ingest(raw[1]) is not None
    assert feed.health(_at(BASE + 60))["alerts"] == []

    # No health, entry or snapshot read happens during the gap: only a later arrival.
    assert feed.ingest(_gap_after(raw, 1800)) is not None

    alerts = feed.health(_at(BASE + 60))["alerts"]
    assert len(alerts) == 1
    assert alerts[0]["type"] == "provider_stale"
    assert alerts[0]["age_seconds"] == 1800.0
    # Reading again does not raise the same transition twice.
    assert len(feed.health(_at(BASE + 60))["alerts"]) == 1


def test_no_event_without_timestamp_passes_unknown_feed() -> None:
    feed = FirehoseFeed()
    assert feed.fresh_events(_at(BASE)) == ()
    health = feed.health(_at(BASE))
    assert health["last_event_age_seconds"] is None
    assert health["stale"] is True


def test_snapshot_then_deltas_for_api_surface(
    firehose_sample: list[dict[str, Any]],
) -> None:
    raw = firehose_sample
    feed = FirehoseFeed()
    events = feed.replay([raw[0], raw[1]])
    assert len(events) == 2
    snap = feed.snapshot(_at(BASE + 60))
    assert snap["source"]["name"] == SOURCE_NAME
    assert snap["stale"] is False
    assert len(snap["recent"]) == 2
    # One row per pair: each record carries its own mint, so both appear.
    assert {row["pair"] for row in snap["coins"]} == {raw[0]["mint"], raw[1]["mint"]}
    # Snapshot rows carry the contract's source and a read-time age.
    assert snap["recent"][0]["source"]["name"] == SOURCE_NAME
    assert isinstance(snap["recent"][0]["age_seconds"], float)
    assert snap["health"]["queue"]["maxsize"] == 500


async def test_async_stream_yields_snapshot_then_delta(
    firehose_sample: list[dict[str, Any]],
) -> None:
    raw = firehose_sample
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
    # A delta is stamped with the clock it is actually read at, not its own timestamp:
    # the fixture record is years old, so the row must not report a fresh-looking zero.
    observed = datetime.fromisoformat(second["event"]["observed_at"])
    assert second["event"]["age_seconds"] == pytest.approx(
        max(0.0, (datetime.now(UTC) - observed).total_seconds()), abs=5.0
    )
    assert second["event"]["age_seconds"] > DEFAULT_STALE_BUDGET_SECONDS
    assert second["event"]["stale"] is True
    await stream.aclose()


def test_duplicate_signature_is_counted_not_emitted_twice(
    firehose_sample: list[dict[str, Any]],
) -> None:
    raw = firehose_sample
    feed = FirehoseFeed()
    assert feed.ingest(raw[1]) is not None
    assert feed.ingest(raw[1]) is None
    health = feed.health(_at(BASE + 60))
    assert health["decoded"] == 1
    assert health["duplicates"] == 1
    assert len(feed.snapshot(_at(BASE + 60))["recent"]) == 1


def test_reconnect_and_reject_counters_are_visible(
    firehose_sample: list[dict[str, Any]],
) -> None:
    raw = firehose_sample
    feed = FirehoseFeed()
    assert feed.ingest(raw[1]) is not None
    assert feed.ingest({"kind": "trade", "mint": "x"}) is None
    feed.record_reconnect()
    health = feed.health(_at(BASE + 60))
    assert health["decoded"] == 1
    assert health["rejected"] == 1
    assert health["reconnects"] == 1
    assert health["decoded_ratio"] == 0.5


def test_contract_observation_survives_the_ingest_recorder(
    firehose_sample: list[dict[str, Any]], tmp_path: Path
) -> None:
    """A firehose observation records and replays as the one core contract."""
    from ezchain_ingest import ReplayHarness, read_events, write_events

    raw = firehose_sample
    feed = FirehoseFeed()
    events = feed.replay([raw[0], raw[1], raw[1]])
    # The replay harness sees contract events, not the wire wrapper.
    assert all(isinstance(event.event, ezchain_core.MarketEvent) for event in events)
    path = tmp_path / "firehose.jsonl"
    assert write_events(path, [event.event for event in events]) == 2
    recorded = read_events(path)
    assert [event.pair for event in recorded] == [raw[0]["mint"], raw[1]["mint"]]
    assert recorded[1].direction == "buy"
    now = _at(BASE + 60)
    first = ReplayHarness(path).derive(now)
    second = ReplayHarness(path).derive(now)
    assert first == second
    # A recorded file carries no freshness verdict; the reader derives it.
    assert "stale" not in path.read_text(encoding="utf-8")


def test_adapter_is_read_only() -> None:
    feed = FirehoseFeed()
    for forbidden in ("sign", "trade", "execute", "order", "buy", "sell"):
        assert not hasattr(feed, forbidden), forbidden


def test_dedupe_window_stays_bounded(monkeypatch):
    """_seen is a window, not an archive: it never exceeds the cap."""
    import ezchain_adapters.firehose as fh

    monkeypatch.setattr(fh, "DEDUPE_WINDOW_LIMIT", 16)
    feed = fh.FirehoseFeed()
    payload = {"kind": "create", "mint": "", "timestamp": BASE, "signature": ""}
    seen = []
    for i in range(40):
        payload = dict(payload, mint=f"m{i}", timestamp=BASE + i, signature=f"sig{i}")
        seen.append(feed.ingest(payload))
    assert all(x is not None for x in seen)
    assert feed.health()["decoded"] == 40
    assert len(feed._seen) <= 16


def test_dedupe_window_prunes_oldest(monkeypatch):
    """Once pruned, an old signature may be accepted again (window semantics)."""
    import ezchain_adapters.firehose as fh

    monkeypatch.setattr(fh, "DEDUPE_WINDOW_LIMIT", 2)
    feed = fh.FirehoseFeed()
    feed.ingest({"kind": "create", "mint": "m0", "timestamp": BASE + 1, "signature": "s0"})
    feed.ingest({"kind": "create", "mint": "m1", "timestamp": BASE + 2, "signature": "s1"})
    # m2 pushes the window past its cap and prunes the oldest signature (s0)
    feed.ingest({"kind": "create", "mint": "m2", "timestamp": BASE + 3, "signature": "s2"})
    again = feed.ingest({"kind": "create", "mint": "m3", "timestamp": BASE + 4, "signature": "s0"})
    # s0 was pruned; this ingest is accepted fresh, no duplicate counted
    assert again is not None
    assert feed.health()["duplicates"] == 0
