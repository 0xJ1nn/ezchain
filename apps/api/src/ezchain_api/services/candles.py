"""Candle history: persisted OHLCV per token, filled from GeckoTerminal on demand.

Why a service and not router code: a strategy needs the *same* bar for the same
(chain, token, timeframe, time) on every run, and a backtest needs to replay it. The
series is therefore stored in ``market_candles`` and served from there; upstream is only
asked for the range that is missing. Two rules are enforced here, once, for every caller:

* **The forming bar is dropped.** GeckoTerminal returns the bar that is still being
  built. An indicator computed on it changes under the same timestamp, so it never
  leaves this module and is never stored.
* **Unknown is not zero.** A bar with no volume keeps ``volume=None`` and a token with no
  pool returns an empty series with a reason code — never a fabricated flat line.

``fixture`` mode is the one deliberate exception: it synthesizes a deterministic series
labelled ``fixture`` and never writes to the table, so a demo cannot be mistaken for
market data.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ezchain_adapters import CandlesUnsupported, NotFound, get_chain, timeframe_seconds
from ezchain_adapters.geckoterminal import BASE as GECKOTERMINAL_BASE
from ezchain_adapters.geckoterminal import pool_url
from ezchain_core import Candle, PairInfo, SourceRef, TokenSnapshot

from ..models import MarketCandleRow

MAX_LIMIT = 1_000
STALE_TIMEFRAMES = 2
SOURCE_NAME = "geckoterminal"
FIXTURE_SOURCE_NAME = "fixture"
REASON_NO_POOL = "NO_POOL"
REASON_EMPTY = "CANDLES_EMPTY"
PRICE_QUANTUM = Decimal("0.00000001")
VOLUME_QUANTUM = Decimal("0.01")


@runtime_checkable
class CandleSource(Protocol):
    """The one candle call this service makes; ``GeckoTerminalClient`` satisfies it."""

    async def ohlcv(
        self,
        chain_id: str,
        pool_address: str,
        token_address: str,
        timeframe: str,
        *,
        limit: int = 300,
        before: datetime | int | None = None,
    ) -> tuple[Candle, ...]: ...


@runtime_checkable
class TokenSource(Protocol):
    """The token lookup this service uses to find the deepest pool."""

    async def get_token(self, chain: str, address: str) -> TokenSnapshot: ...


@dataclass(frozen=True)
class CandleSeries:
    """One token's closed bars plus where they came from, oldest first."""

    chain: str
    token_address: str
    timeframe: str
    candles: tuple[Candle, ...]
    source: SourceRef
    stale: bool
    pool_address: str | None = None
    reason: str | None = None

    @property
    def latest_open_time(self) -> datetime | None:
        return None if not self.candles else self.candles[-1].open_time

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain": self.chain,
            "token_address": self.token_address,
            "timeframe": self.timeframe,
            "pool_address": self.pool_address,
            "candles": [candle.to_dict() for candle in self.candles],
            "source": self.source.to_dict(),
            "stale": self.stale,
            "reason": self.reason,
        }


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _floor(now: datetime, seconds: int) -> datetime:
    """Start of the timeframe bucket containing ``now`` (UTC)."""
    stamp = int(_aware(now).timestamp())
    return datetime.fromtimestamp(stamp - stamp % seconds, tz=UTC)


def latest_closed_open(now: datetime, seconds: int) -> datetime:
    """Open time of the most recent bar that is already closed at ``now``."""
    return _floor(now, seconds) - timedelta(seconds=seconds)


def _is_stale(candles: tuple[Candle, ...], now: datetime, seconds: int) -> bool:
    """Two whole bar lengths with no new bar means the series stopped advancing."""
    if not candles:
        return False
    return _aware(now) - _aware(candles[-1].open_time) > timedelta(
        seconds=STALE_TIMEFRAMES * seconds
    )


def _deepest_pool(snapshot: TokenSnapshot) -> str | None:
    """Highest-liquidity pair, unknown liquidity last. ``None`` when there is no pair."""
    best: PairInfo | None = None
    for pair in snapshot.pairs:
        if not pair.pair_address:
            continue
        if best is None:
            best = pair
            continue
        candidate = (pair.liquidity_usd is not None, pair.liquidity_usd or Decimal(0))
        current = (best.liquidity_usd is not None, best.liquidity_usd or Decimal(0))
        if candidate > current:
            best = pair
    return None if best is None else best.pair_address


def _source(meta_name: str, url: str | None, observed_at: datetime) -> SourceRef:
    return SourceRef(meta_name, url, observed_at)


def _row_candle(row: MarketCandleRow, seconds: int) -> Candle:
    return Candle(
        open_time=_aware(row.open_time),
        open=Decimal(row.open),
        high=Decimal(row.high),
        low=Decimal(row.low),
        close=Decimal(row.close),
        volume=None if row.volume is None else Decimal(row.volume),
        timeframe_seconds=seconds,
    )


def stored_candles(
    session: Session,
    *,
    chain: str,
    token_address: str,
    timeframe: str,
    limit: int,
    latest_open: datetime,
) -> tuple[Candle, ...]:
    """Closed bars from the table, oldest first, newest ``limit`` only."""
    seconds = timeframe_seconds(timeframe)
    statement = (
        select(MarketCandleRow)
        .where(
            MarketCandleRow.chain == chain,
            MarketCandleRow.token_address == token_address,
            MarketCandleRow.timeframe == timeframe,
            MarketCandleRow.open_time <= latest_open,
        )
        .order_by(MarketCandleRow.open_time.desc())
        .limit(max(1, limit))
    )
    rows = list(session.scalars(statement))
    return tuple(_row_candle(row, seconds) for row in reversed(rows))


def stored_fetched_at(
    session: Session, *, chain: str, token_address: str, timeframe: str
) -> datetime | None:
    """When the stored series was last observed at its source.

    A cached read must not claim ``observed_at = now``: the source age is part of the
    evidence (CONTRACT I9), so it comes from the rows.
    """
    value = session.scalar(
        select(func.max(MarketCandleRow.fetched_at)).where(
            MarketCandleRow.chain == chain,
            MarketCandleRow.token_address == token_address,
            MarketCandleRow.timeframe == timeframe,
        )
    )
    if value is None:
        return None
    return _aware(value) if isinstance(value, datetime) else None


def _values(
    *,
    chain: str,
    token_address: str,
    pool_address: str,
    timeframe: str,
    candles: tuple[Candle, ...],
    fetched_at: datetime,
) -> list[dict[str, Any]]:
    return [
        {
            "chain": chain,
            "token_address": token_address,
            "pool_address": pool_address,
            "timeframe": timeframe,
            "open_time": _aware(candle.open_time),
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
            "source": SOURCE_NAME,
            "fetched_at": fetched_at,
        }
        for candle in candles
    ]


def _conflict_set(statement: Any) -> dict[str, Any]:
    excluded = statement.excluded
    return {
        "pool_address": excluded.pool_address,
        "open": excluded.open,
        "high": excluded.high,
        "low": excluded.low,
        "close": excluded.close,
        "volume": excluded.volume,
        "source": excluded.source,
        "fetched_at": excluded.fetched_at,
    }


def write_candles(session: Session, values: list[dict[str, Any]]) -> int:
    """Upsert bars idempotently: one row per (chain, token, timeframe, open_time).

    A re-fetch of a bar the venue revised updates it in place. The unique key is the same
    one the table declares, so a concurrent writer converges instead of duplicating.
    """
    if not values:
        return 0
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgres_insert(MarketCandleRow)
        session.execute(
            statement.on_conflict_do_update(
                index_elements=["chain", "token_address", "timeframe", "open_time"],
                set_=_conflict_set(statement),
            ),
            values,
        )
    elif dialect == "sqlite":
        sqlite_statement = sqlite_insert(MarketCandleRow)
        session.execute(
            sqlite_statement.on_conflict_do_update(
                index_elements=["chain", "token_address", "timeframe", "open_time"],
                set_=_conflict_set(sqlite_statement),
            ),
            values,
        )
    else:  # pragma: no cover - only Postgres and SQLite are supported deployments
        raise RuntimeError(f"candle storage does not support dialect {dialect}")
    return len(values)


def synthetic_candles(
    chain: str,
    token_address: str,
    timeframe: str,
    limit: int,
    now: datetime,
) -> tuple[Candle, ...]:
    """Deterministic fixture series: same inputs, same bars, no network and no table.

    The seed is the identity of the request, so a page reload shows the same history and
    a test can assert exact values. Bars are aligned to the timeframe grid and the last
    one is the latest *closed* bar.
    """
    seconds = timeframe_seconds(timeframe)
    seed = int.from_bytes(
        hashlib.sha256(f"{chain}:{token_address}:{timeframe}".encode()).digest()[:8], "big"
    )
    rng = random.Random(seed)
    price = Decimal(1) + Decimal(rng.randrange(1, 10_000)) / Decimal(1_000)
    start = _floor(now, seconds) - timedelta(seconds=seconds * limit)
    bars: list[Candle] = []
    for index in range(limit):
        opened = start + timedelta(seconds=seconds * index)
        change = Decimal(rng.randrange(-400, 401)) / Decimal(10_000)
        close = max(PRICE_QUANTUM, (price * (Decimal(1) + change)).quantize(PRICE_QUANTUM))
        spread_up = Decimal(rng.randrange(0, 150)) / Decimal(10_000)
        spread_down = Decimal(rng.randrange(0, 150)) / Decimal(10_000)
        high = (max(price, close) * (Decimal(1) + spread_up)).quantize(PRICE_QUANTUM)
        low = min(price, close) * (Decimal(1) - spread_down)
        low = max(PRICE_QUANTUM, min(low, min(price, close)).quantize(PRICE_QUANTUM))
        volume = (Decimal(rng.randrange(100_000, 100_000_000)) / Decimal(100)).quantize(
            VOLUME_QUANTUM
        )
        bars.append(
            Candle(
                open_time=opened,
                open=price.quantize(PRICE_QUANTUM),
                high=high,
                low=low,
                close=close,
                volume=volume,
                timeframe_seconds=seconds,
            )
        )
        price = close
    return tuple(bars)


async def get_candles(
    session: Session,
    client: CandleSource,
    snapshot_source: TokenSource,
    chain: str,
    token_address: str,
    timeframe: str,
    limit: int,
    now: datetime,
    *,
    data_mode: str = "live",
) -> CandleSeries:
    """Closed candle history for one token, from the table when it is current.

    The series is served from ``market_candles`` when its newest stored bar is the latest
    closed bar for ``now`` and it holds ``limit`` bars. Otherwise the missing tail is
    fetched and older pages are filled in only when the stored series is short.
    """
    meta = get_chain(chain)
    if meta is None:
        raise NotFound(f"unknown chain: {chain}")
    if not meta.candles:
        raise CandlesUnsupported(f"{chain} has no candle history provider")
    seconds = timeframe_seconds(timeframe)
    wanted = max(1, min(limit, MAX_LIMIT))
    current = _aware(now)
    newest_closed = latest_closed_open(current, seconds)

    if data_mode == "fixture":
        fixture = synthetic_candles(chain, token_address, timeframe, wanted, current)
        return CandleSeries(
            chain=chain,
            token_address=token_address,
            timeframe=timeframe,
            candles=fixture,
            source=_source(FIXTURE_SOURCE_NAME, None, current),
            stale=_is_stale(fixture, current, seconds),
            pool_address=None,
            reason=None if fixture else REASON_EMPTY,
        )

    try:
        snapshot = await snapshot_source.get_token(chain, token_address)
    except NotFound:
        pool: str | None = None
    else:
        pool = _deepest_pool(snapshot)
    if pool is None:
        return CandleSeries(
            chain=chain,
            token_address=token_address,
            timeframe=timeframe,
            candles=(),
            source=_source(SOURCE_NAME, GECKOTERMINAL_BASE, current),
            stale=False,
            pool_address=None,
            reason=REASON_NO_POOL,
        )

    stored = stored_candles(
        session,
        chain=chain,
        token_address=token_address,
        timeframe=timeframe,
        limit=wanted,
        latest_open=newest_closed,
    )
    written = 0
    if not stored or stored[-1].open_time != newest_closed:
        # The tail is missing (first call, or a bar just closed): fetch it from now backwards.
        fetched = await client.ohlcv(
            chain, pool, token_address, timeframe, limit=wanted, before=None
        )
        written += write_candles(
            session,
            _values(
                chain=chain,
                token_address=token_address,
                pool_address=pool,
                timeframe=timeframe,
                candles=tuple(candle for candle in fetched if candle.is_closed(current)),
                fetched_at=current,
            ),
        )
        session.flush()
        stored = stored_candles(
            session,
            chain=chain,
            token_address=token_address,
            timeframe=timeframe,
            limit=wanted,
            latest_open=newest_closed,
        )

    if stored and len(stored) < wanted:
        # The series is current but short: one bounded page backwards fills the head.
        older = await client.ohlcv(
            chain,
            pool,
            token_address,
            timeframe,
            limit=wanted - len(stored),
            before=stored[0].open_time,
        )
        written += write_candles(
            session,
            _values(
                chain=chain,
                token_address=token_address,
                pool_address=pool,
                timeframe=timeframe,
                candles=tuple(
                    candle
                    for candle in older
                    if candle.is_closed(current) and candle.open_time < stored[0].open_time
                ),
                fetched_at=current,
            ),
        )
        session.flush()
        stored = stored_candles(
            session,
            chain=chain,
            token_address=token_address,
            timeframe=timeframe,
            limit=wanted,
            latest_open=newest_closed,
        )

    if written:
        session.commit()

    observed_at = current
    if not written:
        observed_at = (
            stored_fetched_at(
                session, chain=chain, token_address=token_address, timeframe=timeframe
            )
            or current
        )

    return CandleSeries(
        chain=chain,
        token_address=token_address,
        timeframe=timeframe,
        candles=stored,
        source=_source(SOURCE_NAME, pool_url(meta, pool), observed_at),
        stale=_is_stale(stored, current, seconds),
        pool_address=pool,
        reason=None if stored else REASON_EMPTY,
    )


__all__ = [
    "CandleSeries",
    "CandleSource",
    "MAX_LIMIT",
    "REASON_EMPTY",
    "REASON_NO_POOL",
    "TokenSource",
    "get_candles",
    "latest_closed_open",
    "stored_candles",
    "stored_fetched_at",
    "synthetic_candles",
    "write_candles",
]
