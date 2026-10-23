"""Backtest orchestration and persistence.

The replay itself is :func:`ezchain_core.graph.run_backtest`; this module only obtains the
closed bars, maps the immutable core result onto the HTTP contract and stores the artifact.
No accounting or fill logic is reimplemented here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_core.graph import (
    TIMEFRAME_MINUTES,
    BacktestResult,
    GraphSchemaError,
    parse_document,
    run_backtest,
    validate,
)
from ezchain_core.graph import Candle as GraphCandle
from ezchain_core.types import TokenSnapshot

from ..models import BacktestRow, new_id
from .candles import CandleSeries, CandleSource, TokenSource, get_candles

BACKTEST_LIST_LIMIT = 100
BACKTEST_LABEL = "BACKTEST"


class BacktestRequestError(ValueError):
    """A request the API refuses before running anything. ``status`` maps to HTTP."""

    def __init__(self, code: str, message: str, *, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _snapshot_template(chain: str, token_address: str, symbol: str) -> TokenSnapshot:
    """Only identity and the per-bar close are known during a replay.

    Current liquidity, volume and change fields are deliberately *not* injected into a
    historical tick: that would be look-ahead wearing a snapshot's clothes. Nodes that
    read them stay unknown (CONTRACT I3), and the harness declares a candle-only data
    source.
    """
    return TokenSnapshot(
        chain=chain,
        address=token_address,
        symbol=symbol or token_address,
        name=symbol or token_address,
        price_usd=None,
        liquidity_usd=None,
        volume_24h_usd=None,
        price_change_m5=None,
        price_change_h1=None,
        price_change_h6=None,
        price_change_h24=None,
        pair_created_at=None,
    )


def _graph_candles(series: CandleSeries) -> tuple[GraphCandle, ...]:
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


async def load_candles(
    *,
    session: Session,
    candle_source: CandleSource,
    snapshot_source: TokenSource,
    chain: str,
    token_address: str,
    document: object,
    primary_timeframe: str,
    bars: int,
    now: datetime,
    data_mode: str,
) -> tuple[dict[str, tuple[GraphCandle, ...]], dict[str, str]]:
    """Fetch every timeframe the graph references, closed bars only."""
    report = validate(document)
    if not report.ok:
        raise BacktestRequestError("STRATEGY_GRAPH_INVALID", "the graph is invalid")
    timeframes = {
        *report.warmup_bars,
        primary_timeframe,
    }
    candles: dict[str, tuple[GraphCandle, ...]] = {}
    sources: dict[str, str] = {}
    for timeframe in sorted(timeframes, key=lambda name: TIMEFRAME_MINUTES.get(name, 10_000)):
        series = await get_candles(
            session,
            candle_source,
            snapshot_source,
            chain,
            token_address,
            timeframe,
            bars,
            now,
            data_mode=data_mode,
        )
        candles[timeframe] = _graph_candles(series)
        sources[timeframe] = series.source.name
    primary = candles.get(primary_timeframe, ())
    if not primary:
        raise BacktestRequestError(
            "BACKTEST_NO_CANDLES",
            f"no closed {primary_timeframe} bars are available for this token",
            status=422,
        )
    return candles, sources


def _equity(result: BacktestResult) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    peak: Decimal | None = None
    for point in result.equity_curve:
        equity = point.equity_usd
        if peak is None or equity > peak:
            peak = equity
        drawdown = Decimal(0) if peak is None or peak <= 0 else (equity / peak - 1) * 100
        points.append(
            {
                "t": point.time.isoformat(),
                "equity_usd": str(point.equity_usd),
                "drawdown_pct": str(drawdown),
            }
        )
    return points


def _trades(result: BacktestResult) -> list[dict[str, Any]]:
    """Closed round trips, derived from the core fills.

    The core harness records individual fills; the HTTP contract shows entry and exit as
    one row. An open position is not a closed trade and is not fabricated into one — the
    response's final quantity and metrics carry it instead.
    """
    trades: list[dict[str, Any]] = []
    quantity = Decimal(0)
    basis = Decimal(0)
    entry_fees = Decimal(0)
    entry_time: datetime | None = None

    for fill in result.trades:
        if fill.side == "buy":
            if quantity <= 0:
                entry_time = fill.time
            quantity += fill.quantity
            basis += fill.notional_usd
            entry_fees += fill.fee_usd
            continue
        closed_quantity = min(fill.quantity, quantity)
        if closed_quantity <= 0 or entry_time is None or quantity <= 0:
            continue
        portion = closed_quantity / quantity
        entry_price = basis / quantity
        allocated_entry_fee = entry_fees * portion
        pnl = fill.pnl_usd
        if pnl is not None and fill.quantity > 0 and closed_quantity != fill.quantity:
            pnl = pnl * (closed_quantity / fill.quantity)
        notional = entry_price * closed_quantity
        trades.append(
            {
                "entry_time": entry_time.isoformat(),
                "exit_time": fill.time.isoformat(),
                "side": "buy",
                "qty": str(closed_quantity),
                "entry_price": str(entry_price),
                "exit_price": str(fill.price_usd),
                "pnl_usd": None if pnl is None else str(pnl),
                "pnl_pct": None if pnl is None or notional <= 0 else str(pnl / notional * 100),
                "fees_usd": str(allocated_entry_fee + fill.fee_usd),
                "reason": fill.node,
            }
        )
        basis -= entry_price * closed_quantity
        entry_fees -= allocated_entry_fee
        quantity -= closed_quantity
        if quantity <= 0:
            quantity = Decimal(0)
            basis = Decimal(0)
            entry_fees = Decimal(0)
            entry_time = None
    return trades


def _result_payload(result: BacktestResult) -> dict[str, Any]:
    metrics = result.metrics.to_dict()
    return {
        "label": BACKTEST_LABEL,
        "data_source": result.data_source,
        "fill_model": result.fill_model,
        "metrics": metrics,
        "equity": _equity(result),
        "trades": _trades(result),
        "node_hits": dict(result.node_hits),
        "final_cash_usd": str(result.final_cash_usd),
        "final_quantity": str(result.final_quantity),
        "final_equity_usd": str(result.final_equity_usd),
        "warmup_bars": dict(result.warmup_bars),
    }


def _out(row: BacktestRow) -> dict[str, Any]:
    result = dict(row.result or {})
    result["id"] = row.id
    result["created_at"] = _aware(row.created_at).isoformat()
    result["request"] = dict(row.request or {})
    result.setdefault("label", BACKTEST_LABEL)
    result.setdefault("metrics", {})
    result.setdefault("equity", [])
    result.setdefault("trades", [])
    result.setdefault("node_hits", {})
    return result


async def create_backtest(
    *,
    session: Session,
    graph: object,
    strategy_id: str | None,
    chain: str,
    token_address: str,
    timeframe: str,
    bars: int,
    fee_bps: int,
    slippage_bps: int,
    starting_cash_usd: Decimal,
    max_position_usd: Decimal | None,
    symbol: str,
    candle_source: CandleSource,
    snapshot_source: TokenSource,
    data_mode: str,
    now: datetime,
) -> dict[str, Any]:
    """Run one replay, persist the immutable artifact and return the contract body."""
    try:
        document = parse_document(graph)
    except GraphSchemaError as exc:
        raise BacktestRequestError("STRATEGY_GRAPH_INVALID", exc.message, status=422) from exc
    report = validate(document)
    if not report.ok:
        raise BacktestRequestError("STRATEGY_GRAPH_INVALID", "the graph is invalid", status=422)
    if bars > 1_000:
        raise BacktestRequestError("BACKTEST_BARS_LIMIT", "bars must be <= 1000")

    candles, sources = await load_candles(
        session=session,
        candle_source=candle_source,
        snapshot_source=snapshot_source,
        chain=chain,
        token_address=token_address,
        document=document,
        primary_timeframe=timeframe,
        bars=bars,
        now=now,
        data_mode=data_mode,
    )
    template = _snapshot_template(chain, token_address, symbol)
    try:
        result = run_backtest(
            document,
            candles,
            primary_timeframe=timeframe,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            starting_cash_usd=starting_cash_usd,
            snapshot_template=template,
            max_position_usd=max_position_usd,
        )
    except ValueError as exc:
        raise BacktestRequestError("BACKTEST_INVALID", str(exc), status=422) from exc

    request: dict[str, Any] = {
        "graph": document.to_obj(),
        "strategy_id": strategy_id,
        "chain": chain,
        "token_address": token_address,
        "timeframe": timeframe,
        "bars": bars,
        "fee_bps": fee_bps,
        "slippage_bps": slippage_bps,
        "starting_cash_usd": str(starting_cash_usd),
        "data_mode": data_mode,
        "candle_sources": sources,
    }
    payload = _result_payload(result)
    row = BacktestRow(
        id=new_id(),
        strategy_id=strategy_id,
        request=request,
        result=payload,
        created_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return _out(row)


def list_backtests(session: Session, *, strategy_id: str | None = None) -> list[dict[str, Any]]:
    statement = (
        select(BacktestRow).order_by(BacktestRow.created_at.desc()).limit(BACKTEST_LIST_LIMIT)
    )
    if strategy_id is not None:
        statement = statement.where(BacktestRow.strategy_id == strategy_id)
    rows = list(session.scalars(statement))
    return [
        {
            "id": row.id,
            "created_at": _aware(row.created_at).isoformat(),
            "request": dict(row.request or {}),
            "metrics": dict((row.result or {}).get("metrics", {})),
        }
        for row in rows
    ]


def get_backtest(session: Session, backtest_id: str) -> dict[str, Any] | None:
    row = session.get(BacktestRow, backtest_id)
    return None if row is None else _out(row)


__all__ = [
    "BACKTEST_LABEL",
    "BACKTEST_LIST_LIMIT",
    "BacktestRequestError",
    "create_backtest",
    "get_backtest",
    "list_backtests",
    "load_candles",
]
