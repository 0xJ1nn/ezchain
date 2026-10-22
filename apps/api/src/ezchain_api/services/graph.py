"""Graph context assembly for the paper engine and the dry-run endpoint.

One builder serves both callers, so a dry run and a real scheduler tick see the same
snapshot, the same closed bars and the same ledger-derived position. The evaluator itself
stays in :mod:`ezchain_core.graph`; this module only assembles its inputs and never
places an order.

Unknown is not zero (CONTRACT I3): a missing snapshot price, mark or candle series is
``None``/empty in the ``EvalContext`` so the evaluator fails closed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_core.graph import TIMEFRAME_MINUTES, EvalContext, parse_document, validate
from ezchain_core.graph import Candle as GraphCandle
from ezchain_core.strategy import GraphParams, validate_params
from ezchain_core.types import TokenSnapshot
from ezchain_engine import latest_graph_vars, position_state

from ..models import PaperFillRow, StrategyRow
from . import paper
from .candles import CandleSeries, CandleSource, TokenSource, get_candles

#: How many bars beyond an indicator's warmup the context keeps. The extra bars let a
#: cross/ROC read the previous value as well as the current one.
CONTEXT_EXTRA_BARS = 100
MIN_CONTEXT_BARS = 2
MAX_CONTEXT_BARS = 1_000


@dataclass(frozen=True)
class CandleSummary:
    """What the dry-run response may say about one timeframe without inventing values."""

    bars: int
    last_close_time: datetime | None
    stale: bool


@dataclass(frozen=True)
class GraphTick:
    """A frozen context plus the summaries used by the dry-run read model."""

    context: EvalContext
    candles: dict[str, CandleSummary]


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _limit_for(warmup: int) -> int:
    return max(MIN_CONTEXT_BARS, min(MAX_CONTEXT_BARS, warmup + CONTEXT_EXTRA_BARS))


def _row_candles(series: CandleSeries) -> tuple[GraphCandle, ...]:
    return tuple(
        GraphCandle(
            open_time=_aware(candle.open_time),
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.volume,
        )
        for candle in series.candles
    )


def _summary(series: CandleSeries) -> CandleSummary:
    last = series.candles[-1].close_time if series.candles else None
    return CandleSummary(
        bars=len(series.candles),
        last_close_time=None if last is None else _aware(last),
        stale=series.stale,
    )


def _fill_timing(
    session: Session, chain: str, token_address: str
) -> tuple[datetime | None, datetime | None, list[PaperFillRow]]:
    """Last fill, current entry time and the fills themselves, from the fill journal.

    The position is shared by every strategy on this paper account, so this is the
    account's position timing, never a per-strategy guess.
    """
    fills = list(
        session.scalars(
            select(PaperFillRow)
            .where(
                PaperFillRow.chain == chain,
                PaperFillRow.token_address == token_address,
            )
            .order_by(PaperFillRow.created_at, PaperFillRow.id)
        )
    )
    quantity = Decimal(0)
    entry_at: datetime | None = None
    for fill in fills:
        stamp = _aware(fill.created_at)
        quantity_delta = Decimal(str(fill.quantity))
        if fill.side == "buy":
            if quantity <= 0:
                entry_at = stamp
            quantity += quantity_delta
        else:
            quantity -= quantity_delta
            if quantity <= 0:
                quantity = Decimal(0)
                entry_at = None
    last_fill_at = None if not fills else _aware(fills[-1].created_at)
    return last_fill_at, entry_at, fills


def _peak_since_entry(
    entry_at: datetime | None,
    fills: list[PaperFillRow],
    candles: Mapping[str, tuple[GraphCandle, ...]],
    price: Decimal | None,
) -> Decimal | None:
    """Highest observed price since entry, from closes and fill prices.

    ``None`` while flat: a peak with no position is meaningless, not zero. The series is
    sampled (bar closes) so the value is a lower bound, never a fabricated high.
    """
    if entry_at is None:
        return None
    values: list[Decimal] = []
    if price is not None:
        values.append(price)
    for bars in candles.values():
        values.extend(bar.close for bar in bars if bar.open_time >= entry_at)
    values.extend(
        Decimal(str(fill.price_usd)) for fill in fills if _aware(fill.created_at) >= entry_at
    )
    return max(values) if values else None


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


async def build_tick(
    *,
    session: Session,
    candle_source: CandleSource,
    snapshot_source: TokenSource,
    document: object,
    chain: str,
    token_address: str,
    snapshot: TokenSnapshot,
    now: datetime,
    max_position_usd: Decimal | None,
    data_mode: str,
    variables: Mapping[str, Decimal | None] | None = None,
) -> GraphTick:
    """Build one frozen ``EvalContext`` for ``now`` from closed bars and the ledger."""
    document = parse_document(document)
    report = validate(document)
    if not report.ok:
        raise ValueError("cannot build a context for an invalid graph")
    timeframes = tuple(
        sorted(report.warmup_bars, key=lambda name: TIMEFRAME_MINUTES.get(name, 10_000))
    )

    candle_map: dict[str, tuple[GraphCandle, ...]] = {}
    summaries: dict[str, CandleSummary] = {}
    for timeframe in timeframes:
        series = await get_candles(
            session,
            candle_source,
            snapshot_source,
            chain,
            token_address,
            timeframe,
            _limit_for(report.warmup_bars.get(timeframe, 0)),
            now,
            data_mode=data_mode,
        )
        candle_map[timeframe] = _row_candles(series)
        summaries[timeframe] = _summary(series)

    position = position_state(session, chain, token_address, snapshot.symbol)
    account = paper.account_snapshot(session)
    daily_realized = paper.daily_realized_pnl(session)
    last_fill_at, entry_at, fills = _fill_timing(session, chain, token_address)

    price = None if snapshot.stale else snapshot.price_usd
    if position.quantity <= 0:
        position_value = Decimal(0)
        average_cost = None
    elif price is None:
        position_value = None
        average_cost = position.avg_cost_usd
    else:
        position_value = position.quantity * price
        average_cost = position.avg_cost_usd

    peak = _peak_since_entry(entry_at, fills, candle_map, price)
    context = EvalContext(
        now=now,
        snapshot=snapshot,
        candles=candle_map,
        position_quantity=position.quantity,
        position_avg_cost_usd=average_cost,
        position_value_usd=position_value,
        cash_usd=_decimal_or_none(account.get("cash_usd")),
        equity_usd=_decimal_or_none(account.get("equity_usd")),
        daily_realized_pnl_usd=daily_realized,
        open_positions=len(account.get("positions", [])),
        last_fill_at=last_fill_at,
        entry_at=entry_at,
        peak_since_entry_usd=peak,
        max_position_usd=max_position_usd,
        vars=dict(variables or {}),
    )
    return GraphTick(context=context, candles=summaries)


class PaperGraphRuntime:
    """The engine's graph context port, backed by candles and the paper ledger.

    Composition lives in ``apps/api/main.py``; the scheduler only sees the protocol.
    """

    def __init__(
        self,
        candle_source: CandleSource,
        snapshot_source: TokenSource,
        data_mode: str,
    ) -> None:
        self._candle_source = candle_source
        self._snapshot_source = snapshot_source
        self._data_mode = data_mode

    async def build_context(
        self,
        session: Session,
        strategy: StrategyRow,
        *,
        snapshot: TokenSnapshot,
        now: datetime,
    ) -> EvalContext:
        params = validate_params("graph", dict(strategy.params or {}))
        if not isinstance(params, GraphParams):  # pragma: no cover - kind is checked upstream
            raise ValueError("strategy is not a graph")
        tick = await build_tick(
            session=session,
            candle_source=self._candle_source,
            snapshot_source=self._snapshot_source,
            document=params.document,
            chain=strategy.chain,
            token_address=strategy.token_address,
            snapshot=snapshot,
            now=now,
            max_position_usd=params.max_position_usd,
            data_mode=self._data_mode,
            variables=latest_graph_vars(session, strategy.id),
        )
        return tick.context


__all__ = [
    "CandleSummary",
    "GraphTick",
    "PaperGraphRuntime",
    "build_tick",
]
