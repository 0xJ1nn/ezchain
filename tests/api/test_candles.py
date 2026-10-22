"""Candle history service: closed-bar rule, DB reuse, range fill, fixture mode.

No network: the provider is a stub that records its calls. The session is the real SQLite
schema from ``tests/api/conftest.py``, so "second call does not hit the client" is tested
against actual rows rather than an in-memory dict.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ezchain_adapters import CandlesUnsupported, NotFound, UpstreamError
from ezchain_api.models import MarketCandleRow
from ezchain_api.services.candles import (
    REASON_EMPTY,
    REASON_NO_POOL,
    get_candles,
    latest_closed_open,
    stored_candles,
    synthetic_candles,
    write_candles,
)
from ezchain_core import Candle, PairInfo, SourceRef, TokenSnapshot

CHAIN = "ethereum"
TOKEN = "0xtoken"
POOL = "0xpool"
DEEP_POOL = "0xdeeppool"
FIVE_MINUTES = 300
BASE = datetime(2026, 10, 22, 12, 10, tzinfo=UTC)


def bar(open_time: datetime, close: str = "2", volume: str | None = "10") -> Candle:
    price = Decimal(close)
    return Candle(
        open_time=open_time,
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=None if volume is None else Decimal(volume),
        timeframe_seconds=FIVE_MINUTES,
    )


def series(count: int, *, end: datetime, step_seconds: int = FIVE_MINUTES) -> tuple[Candle, ...]:
    """``count`` bars whose last open time is ``end``, oldest first."""
    return tuple(
        bar(end - timedelta(seconds=step_seconds * (count - 1 - index))) for index in range(count)
    )


class FakeCandles:
    """Stub provider. ``pages`` are returned in order; later calls get an empty page."""

    def __init__(self, *pages: tuple[Candle, ...], error: Exception | None = None) -> None:
        self.pages = list(pages)
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def ohlcv(
        self,
        chain_id: str,
        pool_address: str,
        token_address: str,
        timeframe: str,
        *,
        limit: int = 300,
        before: datetime | int | None = None,
    ) -> tuple[Candle, ...]:
        self.calls.append(
            {
                "chain": chain_id,
                "pool": pool_address,
                "token": token_address,
                "timeframe": timeframe,
                "limit": limit,
                "before": before,
            }
        )
        if self.error is not None:
            raise self.error
        return self.pages.pop(0) if self.pages else ()


class FakeSnapshot:
    def __init__(self, pairs: tuple[PairInfo, ...], *, error: Exception | None = None) -> None:
        self.pairs = pairs
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def get_token(self, chain: str, address: str) -> TokenSnapshot:
        self.calls.append((chain, address))
        if self.error is not None:
            raise self.error
        return TokenSnapshot(
            chain=chain,
            address=address,
            symbol="TKN",
            name="Token",
            price_usd=Decimal("2"),
            liquidity_usd=Decimal("1000"),
            volume_24h_usd=None,
            price_change_m5=None,
            price_change_h1=None,
            price_change_h6=None,
            price_change_h24=None,
            pairs=self.pairs,
            source=SourceRef("dexscreener", "https://dexscreener.com", BASE),
            stale=False,
        )


def pair(address: str, liquidity: str | None) -> PairInfo:
    return PairInfo(
        pair_address=address,
        dex="testdex",
        quote_token="USDC",
        price_usd=Decimal("2"),
        liquidity_usd=None if liquidity is None else Decimal(liquidity),
        volume_24h_usd=None,
        url=None,
    )


def snapshot_source(*pairs: PairInfo) -> FakeSnapshot:
    return FakeSnapshot(pairs or (pair(POOL, "1000"), pair(DEEP_POOL, "5000")))


def rows(session: Session) -> list[MarketCandleRow]:
    return list(session.scalars(select(MarketCandleRow).order_by(MarketCandleRow.open_time)))


def count(session: Session) -> int:
    return int(session.scalar(select(func.count(MarketCandleRow.id))) or 0)


async def test_forming_bar_is_never_returned_or_stored(session: Session) -> None:
    # 30s into the 12:10 bar: the newest closed bar opened at 12:05 and closed at 12:10.
    now = datetime(2026, 10, 22, 12, 10, 30, tzinfo=UTC)
    provider = FakeCandles(series(3, end=datetime(2026, 10, 22, 12, 10, tzinfo=UTC)))
    received = await get_candles(session, provider, snapshot_source(), CHAIN, TOKEN, "5m", 3, now)

    assert [c.open_time for c in received.candles] == [
        datetime(2026, 10, 22, 12, 0, tzinfo=UTC),
        datetime(2026, 10, 22, 12, 5, tzinfo=UTC),
    ]
    # The 12:10 bar closes at 12:15, i.e. after `now`: forming, so it must not be stored.
    assert [row.open_time.replace(tzinfo=UTC) for row in rows(session)] == [
        datetime(2026, 10, 22, 12, 0, tzinfo=UTC),
        datetime(2026, 10, 22, 12, 5, tzinfo=UTC),
    ]


@pytest.mark.parametrize(
    "offset_seconds",
    [0, 1, 59, 60, 150, 299, 300, 301, 899, 3_599],
)
async def test_boundary_rule_across_offsets(session: Session, offset_seconds: int) -> None:
    """A bar is closed exactly at ``open_time + timeframe``; anything after is forming."""
    seconds = 3_600
    start = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    now = start + timedelta(seconds=offset_seconds)
    bars = tuple(
        Candle(
            open_time=start + timedelta(seconds=seconds * index),
            open=Decimal("1"),
            high=Decimal("2"),
            low=Decimal("1"),
            close=Decimal("2"),
            volume=None,
            timeframe_seconds=seconds,
        )
        for index in range(3)
    )
    provider = FakeCandles(bars)
    received = await get_candles(session, provider, snapshot_source(), CHAIN, TOKEN, "1h", 3, now)

    expected = [candle for candle in bars if candle.close_time <= now]
    assert list(received.candles) == expected
    assert all(candle.close_time <= now for candle in received.candles)


async def test_second_call_is_served_from_the_table(session: Session) -> None:
    now = BASE
    provider = FakeCandles(series(5, end=latest_closed_open(now, FIVE_MINUTES)))
    snapshots = snapshot_source()

    first = await get_candles(session, provider, snapshots, CHAIN, TOKEN, "5m", 5, now)
    second = await get_candles(session, provider, snapshots, CHAIN, TOKEN, "5m", 5, now)

    assert len(provider.calls) == 1, "the stored series is current; no second request"
    assert first.candles == second.candles
    assert len(second.candles) == 5
    assert count(session) == 5
    # A cached read reports when the bars were observed at the source, not "now".
    assert first.source.observed_at == now
    assert second.source.observed_at == now


async def test_new_bar_triggers_one_tail_fetch_and_upserts(session: Session) -> None:
    now = BASE
    newest = latest_closed_open(now, FIVE_MINUTES)
    provider = FakeCandles(series(3, end=newest), series(3, end=newest + timedelta(minutes=5)))
    snapshots = snapshot_source()

    await get_candles(session, provider, snapshots, CHAIN, TOKEN, "5m", 3, now)
    later = now + timedelta(minutes=5)
    updated = await get_candles(session, provider, snapshots, CHAIN, TOKEN, "5m", 3, later)

    assert len(provider.calls) == 2
    assert provider.calls[1]["before"] is None
    assert updated.candles[-1].open_time == newest + timedelta(minutes=5)
    assert count(session) == 4, "the overlapping bars are updated, never duplicated"


async def test_short_table_is_filled_backwards_with_before(session: Session) -> None:
    now = BASE
    newest = latest_closed_open(now, FIVE_MINUTES)
    tail = series(2, end=newest)
    older = series(3, end=newest - timedelta(minutes=10))
    provider = FakeCandles(tail, older)
    snapshots = snapshot_source()

    await get_candles(session, provider, snapshots, CHAIN, TOKEN, "5m", 2, now)
    filled = await get_candles(session, provider, snapshots, CHAIN, TOKEN, "5m", 5, now)

    assert len(provider.calls) == 2
    assert provider.calls[1]["before"] == tail[0].open_time
    assert provider.calls[1]["limit"] == 3
    assert len(filled.candles) == 5
    assert [c.open_time for c in filled.candles] == sorted(c.open_time for c in filled.candles)


async def test_stale_series_is_flagged(session: Session) -> None:
    now = BASE
    stale_end = latest_closed_open(now, FIVE_MINUTES) - timedelta(minutes=15)
    provider = FakeCandles(series(3, end=stale_end))
    received = await get_candles(session, provider, snapshot_source(), CHAIN, TOKEN, "5m", 3, now)

    assert received.stale is True
    assert received.reason is None


async def test_fresh_series_is_not_stale(session: Session) -> None:
    now = BASE
    provider = FakeCandles(series(3, end=latest_closed_open(now, FIVE_MINUTES)))
    received = await get_candles(session, provider, snapshot_source(), CHAIN, TOKEN, "5m", 3, now)

    assert received.stale is False


async def test_deepest_pool_is_used(session: Session) -> None:
    now = BASE
    provider = FakeCandles(series(1, end=latest_closed_open(now, FIVE_MINUTES)))
    await get_candles(session, provider, snapshot_source(), CHAIN, TOKEN, "5m", 1, now)

    assert provider.calls[0]["pool"] == DEEP_POOL
    assert provider.calls[0]["token"] == TOKEN


async def test_unknown_liquidity_pools_are_ranked_last(session: Session) -> None:
    now = BASE
    provider = FakeCandles(series(1, end=latest_closed_open(now, FIVE_MINUTES)))
    snapshots = FakeSnapshot((pair("0xunknown", None), pair(POOL, "10")))
    await get_candles(session, provider, snapshots, CHAIN, TOKEN, "5m", 1, now)

    assert provider.calls[0]["pool"] == POOL


async def test_no_pool_returns_an_empty_reasoned_series(session: Session) -> None:
    provider = FakeCandles()
    received = await get_candles(session, provider, FakeSnapshot(()), CHAIN, TOKEN, "5m", 5, BASE)

    assert received.candles == ()
    assert received.reason == REASON_NO_POOL
    assert received.pool_address is None
    assert provider.calls == []


async def test_token_without_a_market_is_not_an_error(session: Session) -> None:
    provider = FakeCandles()
    received = await get_candles(
        session,
        provider,
        FakeSnapshot((), error=NotFound("no priced pair")),
        CHAIN,
        TOKEN,
        "5m",
        5,
        BASE,
    )

    assert received.candles == ()
    assert received.reason == REASON_NO_POOL


async def test_empty_provider_response_is_reasoned(session: Session) -> None:
    provider = FakeCandles(())
    received = await get_candles(session, provider, snapshot_source(), CHAIN, TOKEN, "5m", 5, BASE)

    assert received.candles == ()
    assert received.reason == REASON_EMPTY
    assert received.stale is False


async def test_chain_without_a_provider_fails_closed(session: Session) -> None:
    with pytest.raises(CandlesUnsupported):
        await get_candles(
            session, FakeCandles(), snapshot_source(), "osmosis", TOKEN, "5m", 5, BASE
        )


async def test_unknown_chain_is_not_found(session: Session) -> None:
    with pytest.raises(NotFound):
        await get_candles(session, FakeCandles(), snapshot_source(), "nope", TOKEN, "5m", 5, BASE)


async def test_unknown_timeframe_fails_closed(session: Session) -> None:
    with pytest.raises(CandlesUnsupported):
        await get_candles(session, FakeCandles(), snapshot_source(), CHAIN, TOKEN, "7m", 5, BASE)


async def test_upstream_error_propagates(session: Session) -> None:
    provider = FakeCandles(error=UpstreamError("boom"))
    with pytest.raises(UpstreamError):
        await get_candles(session, provider, snapshot_source(), CHAIN, TOKEN, "5m", 5, BASE)


async def test_limit_is_capped_at_one_thousand(session: Session) -> None:
    provider = FakeCandles(series(2, end=latest_closed_open(BASE, FIVE_MINUTES)))
    await get_candles(session, provider, snapshot_source(), CHAIN, TOKEN, "5m", 50_000, BASE)

    assert provider.calls[0]["limit"] == 1_000


async def test_prices_and_unknown_volume_round_trip_exactly(session: Session) -> None:
    now = BASE
    exact = Candle(
        open_time=latest_closed_open(now, FIVE_MINUTES),
        open=Decimal("0.12345678"),
        high=Decimal("0.87654321"),
        low=Decimal("0.00000001"),
        close=Decimal("0.50000000"),
        volume=None,
        timeframe_seconds=FIVE_MINUTES,
    )
    provider = FakeCandles((exact,))
    await get_candles(session, provider, snapshot_source(), CHAIN, TOKEN, "5m", 1, now)

    (row,) = rows(session)
    assert row.open == Decimal("0.12345678")
    assert row.high == Decimal("0.87654321")
    assert row.low == Decimal("0.00000001")
    assert row.close == Decimal("0.50000000")
    assert row.volume is None


async def test_source_carries_the_geckoterminal_page(session: Session) -> None:
    now = BASE
    provider = FakeCandles(series(1, end=latest_closed_open(now, FIVE_MINUTES)))
    received = await get_candles(session, provider, snapshot_source(), CHAIN, TOKEN, "5m", 1, now)

    assert received.source.name == "geckoterminal"
    assert received.source.url == f"https://www.geckoterminal.com/eth/pools/{DEEP_POOL}"
    assert received.source.observed_at == now


async def test_fixture_mode_is_deterministic_and_never_writes(session: Session) -> None:
    provider = FakeCandles()
    snapshots = FakeSnapshot((), error=AssertionError("fixture mode must not call the market"))

    first = await get_candles(
        session, provider, snapshots, CHAIN, TOKEN, "5m", 4, BASE, data_mode="fixture"
    )
    second = await get_candles(
        session, provider, snapshots, CHAIN, TOKEN, "5m", 4, BASE, data_mode="fixture"
    )

    assert first.candles == second.candles
    assert len(first.candles) == 4
    assert first.source.name == "fixture"
    assert first.stale is False
    assert all(candle.is_closed(BASE) for candle in first.candles)
    assert count(session) == 0
    assert provider.calls == []


def test_synthetic_candles_are_stable_and_ohlc_consistent() -> None:
    now = datetime(2026, 10, 22, 12, 7, 30, tzinfo=UTC)
    first = synthetic_candles(CHAIN, TOKEN, "5m", 20, now)
    second = synthetic_candles(CHAIN, TOKEN, "5m", 20, now)

    assert first == second
    assert len(first) == 20
    for candle in first:
        assert candle.low <= min(candle.open, candle.close)
        assert candle.high >= max(candle.open, candle.close)
        assert candle.volume is not None and candle.volume > 0
        assert candle.open_time.second == 0 and candle.open_time.minute % 5 == 0
        assert candle.is_closed(now)


def test_synthetic_candles_differ_per_token() -> None:
    now = datetime(2026, 10, 22, 12, 7, 30, tzinfo=UTC)
    assert synthetic_candles(CHAIN, TOKEN, "5m", 5, now) != synthetic_candles(
        CHAIN, "0xother", "5m", 5, now
    )


def test_write_candles_is_idempotent(session: Session) -> None:
    now = latest_closed_open(BASE, FIVE_MINUTES)
    values = [
        {
            "chain": CHAIN,
            "token_address": TOKEN,
            "pool_address": POOL,
            "timeframe": "5m",
            "open_time": now,
            "open": Decimal("1"),
            "high": Decimal("2"),
            "low": Decimal("1"),
            "close": Decimal("2"),
            "volume": None,
            "source": "geckoterminal",
            "fetched_at": BASE,
        }
    ]
    write_candles(session, values)
    write_candles(session, values)
    session.commit()

    assert count(session) == 1


def test_stored_candles_excludes_the_forming_bar(session: Session) -> None:
    latest_open = latest_closed_open(BASE, FIVE_MINUTES)  # 12:05 closes at 12:10 == BASE
    values = [
        {
            "chain": CHAIN,
            "token_address": TOKEN,
            "pool_address": POOL,
            "timeframe": "5m",
            "open_time": latest_open + timedelta(minutes=5),  # forming
            "open": Decimal("1"),
            "high": Decimal("1"),
            "low": Decimal("1"),
            "close": Decimal("1"),
            "volume": Decimal("1"),
            "source": "geckoterminal",
            "fetched_at": BASE,
        },
        {
            "chain": CHAIN,
            "token_address": TOKEN,
            "pool_address": POOL,
            "timeframe": "5m",
            "open_time": latest_open,
            "open": Decimal("2"),
            "high": Decimal("2"),
            "low": Decimal("2"),
            "close": Decimal("2"),
            "volume": Decimal("2"),
            "source": "geckoterminal",
            "fetched_at": BASE,
        },
    ]
    write_candles(session, values)
    session.commit()

    kept = stored_candles(
        session,
        chain=CHAIN,
        token_address=TOKEN,
        timeframe="5m",
        limit=10,
        latest_open=latest_open,
    )
    assert [candle.open_time for candle in kept] == [latest_open]
