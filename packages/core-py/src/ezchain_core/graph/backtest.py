"""Bar-by-bar replay of the same evaluator (contract section 8).

The harness is deliberately dumb and honest about its assumptions:

* context at bar ``i`` only sees bars whose **close** time is ``<=`` the close of bar
  ``i``; a graph can never see a forming bar;
* an order decided on bar ``i`` fills at the **open of bar ``i + 1``** — no same-bar fill;
* fills pay ``fee_bps`` on the notional and ``slippage_bps`` against the fill price;
* cash, position and cost basis are ``Decimal`` throughout; equity is
  ``cash + quantity * close``;
* the result declares ``BACKTEST``, its data source, fill model, fee and slippage.

Everything in the result is immutable and JSON-safe (Decimals as strings).
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from typing import Any

from ..types import Side, TokenSnapshot
from .catalogue import TIMEFRAME_MINUTES
from .evaluate import Candle, EvalContext, GraphDecision, evaluate_graph
from .schema import parse_document
from .validate import validate

BACKTEST_LABEL = "BACKTEST"
DATA_SOURCE = "provided_candles"
FILL_MODEL = "next_bar_open"

ZERO = Decimal(0)
HUNDRED = Decimal(100)
BPS = Decimal(10_000)
_MONEY = Decimal("0.000001")


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _close_time(bar: Candle, minutes: int) -> datetime:
    return _aware(bar.open_time) + timedelta(minutes=minutes)


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY)


def _pct(value: Decimal) -> Decimal:
    return value.quantize(_MONEY)


@dataclass(frozen=True)
class EquityPoint:
    """Mark-to-market state at one bar close."""

    time: datetime
    price_usd: Decimal
    cash_usd: Decimal
    quantity: Decimal
    position_value_usd: Decimal
    equity_usd: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "time": self.time.isoformat(),
            "price_usd": str(self.price_usd),
            "cash_usd": str(self.cash_usd),
            "quantity": str(self.quantity),
            "position_value_usd": str(self.position_value_usd),
            "equity_usd": str(self.equity_usd),
        }


@dataclass(frozen=True)
class BacktestTrade:
    """One fill. ``pnl_usd`` is set on closing (sell) fills only."""

    time: datetime
    side: Side
    price_usd: Decimal
    quantity: Decimal
    notional_usd: Decimal
    fee_usd: Decimal
    pnl_usd: Decimal | None = None
    node: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "time": self.time.isoformat(),
            "side": self.side,
            "price_usd": str(self.price_usd),
            "quantity": str(self.quantity),
            "notional_usd": str(self.notional_usd),
            "fee_usd": str(self.fee_usd),
            "pnl_usd": None if self.pnl_usd is None else str(self.pnl_usd),
            "node": self.node,
        }


@dataclass(frozen=True)
class BacktestMetrics:
    """Headline numbers. ``None`` means "not defined", never zero."""

    net_pnl_usd: Decimal
    return_pct: Decimal
    max_drawdown_pct: Decimal
    win_rate_pct: Decimal | None
    profit_factor: Decimal | None
    trades: int
    closed_trades: int
    exposure_pct: Decimal
    sharpe: Decimal | None
    fees_usd: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "net_pnl_usd": str(self.net_pnl_usd),
            "return_pct": str(self.return_pct),
            "max_drawdown_pct": str(self.max_drawdown_pct),
            "win_rate_pct": None if self.win_rate_pct is None else str(self.win_rate_pct),
            "profit_factor": None if self.profit_factor is None else str(self.profit_factor),
            "trades": self.trades,
            "closed_trades": self.closed_trades,
            "exposure_pct": str(self.exposure_pct),
            "sharpe": None if self.sharpe is None else str(self.sharpe),
            "fees_usd": str(self.fees_usd),
        }


@dataclass(frozen=True)
class BacktestResult:
    """An immutable backtest. The label is always ``BACKTEST`` — never PAPER or LIVE."""

    primary_timeframe: str
    starting_cash_usd: Decimal
    final_cash_usd: Decimal
    final_quantity: Decimal
    final_equity_usd: Decimal
    equity_curve: tuple[EquityPoint, ...]
    trades: tuple[BacktestTrade, ...]
    metrics: BacktestMetrics
    node_hits: dict[str, int]
    warmup_bars: dict[str, int]
    fee_bps: int
    slippage_bps: int
    label: str = BACKTEST_LABEL
    data_source: str = DATA_SOURCE
    fill_model: str = FILL_MODEL

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "data_source": self.data_source,
            "fill_model": self.fill_model,
            "primary_timeframe": self.primary_timeframe,
            "fee_bps": self.fee_bps,
            "slippage_bps": self.slippage_bps,
            "starting_cash_usd": str(self.starting_cash_usd),
            "final_cash_usd": str(self.final_cash_usd),
            "final_quantity": str(self.final_quantity),
            "final_equity_usd": str(self.final_equity_usd),
            "equity_curve": [point.to_dict() for point in self.equity_curve],
            "trades": [trade.to_dict() for trade in self.trades],
            "metrics": self.metrics.to_dict(),
            "node_hits": dict(self.node_hits),
            "warmup_bars": dict(self.warmup_bars),
        }


@dataclass
class _Book:
    """Mutable paper book used only inside one run."""

    cash: Decimal
    quantity: Decimal = ZERO
    basis: Decimal = ZERO
    entry_at: datetime | None = None
    last_fill_at: datetime | None = None
    peak: Decimal | None = None

    def average_cost(self) -> Decimal | None:
        if self.quantity <= 0:
            return None
        with localcontext() as context:
            context.prec = 28
            return self.basis / self.quantity


def _visible(
    bars: Sequence[Candle], close_times: Sequence[datetime], now: datetime
) -> tuple[Candle, ...]:
    """Bars whose close time is at or before ``now`` — the no-lookahead fence."""
    index = bisect_right(close_times, now)
    return tuple(bars[:index])


def run_backtest(
    doc: object,
    candles_by_tf: Mapping[str, Sequence[Candle]],
    *,
    primary_timeframe: str,
    fee_bps: int,
    slippage_bps: int,
    starting_cash_usd: Decimal,
    snapshot_template: TokenSnapshot,
    max_position_usd: Decimal | None = None,
) -> BacktestResult:
    """Replay a graph over historical candles. Raises ``ValueError`` on an invalid graph."""
    if primary_timeframe not in TIMEFRAME_MINUTES:
        raise ValueError(f"unsupported primary timeframe: {primary_timeframe}")
    if fee_bps < 0 or slippage_bps < 0:
        raise ValueError("fee_bps and slippage_bps cannot be negative")
    if starting_cash_usd <= 0:
        raise ValueError("starting_cash_usd must be > 0")

    document = parse_document(doc)
    report = validate(document)
    if not report.ok:
        codes = ", ".join(sorted({issue.code for issue in report.errors}))
        raise ValueError(f"invalid graph: {codes}")

    for timeframe in candles_by_tf:
        if timeframe not in TIMEFRAME_MINUTES:
            raise ValueError(f"unsupported timeframe: {timeframe}")

    prepared: dict[str, tuple[tuple[Candle, ...], tuple[datetime, ...]]] = {}
    for timeframe, series in candles_by_tf.items():
        minutes = TIMEFRAME_MINUTES[timeframe]
        ordered = tuple(sorted(series, key=lambda bar: _aware(bar.open_time)))
        prepared[timeframe] = (ordered, tuple(_close_time(bar, minutes) for bar in ordered))

    bars, close_times = prepared.get(primary_timeframe, ((), ()))
    fee_rate = Decimal(fee_bps) / BPS
    slippage_rate = Decimal(slippage_bps) / BPS

    book = _Book(cash=starting_cash_usd)
    equity_curve: list[EquityPoint] = []
    trades: list[BacktestTrade] = []
    node_hits: dict[str, int] = {}
    variables: dict[str, Decimal | None] = {}
    pending: dict[str, Any] | None = None
    fees_paid = ZERO
    bars_in_position = 0

    for index, bar in enumerate(bars):
        now = close_times[index]

        # 1. Fill the order decided on the previous bar, at this bar's open.
        if pending is not None:
            fill = _execute(
                book=book,
                effect=pending,
                open_price=bar.open,
                when=_aware(bar.open_time),
                fee_rate=fee_rate,
                slippage_rate=slippage_rate,
            )
            if fill is not None:
                trades.append(fill)
                fees_paid += fill.fee_usd
                book.last_fill_at = fill.time
            pending = None

        # 2. Evaluate on closed bars only.
        snapshot = replace(snapshot_template, price_usd=bar.close, stale=False)
        position_value = book.quantity * bar.close
        context = EvalContext(
            now=now,
            snapshot=snapshot,
            candles={
                timeframe: _visible(series, times, now)
                for timeframe, (series, times) in prepared.items()
            },
            position_quantity=book.quantity,
            position_avg_cost_usd=book.average_cost(),
            position_value_usd=position_value,
            cash_usd=book.cash,
            equity_usd=book.cash + position_value,
            daily_realized_pnl_usd=None,
            open_positions=0 if book.quantity <= 0 else 1,
            last_fill_at=book.last_fill_at,
            entry_at=book.entry_at,
            peak_since_entry_usd=book.peak,
            max_position_usd=max_position_usd,
            vars=dict(variables),
        )
        decision = evaluate_graph(document, context)

        for node_id in decision.trace.get("exec", []):
            node_hits[node_id] = node_hits.get(node_id, 0) + 1
        _apply_set_vars(decision, variables)

        order = next((effect for effect in decision.effects if effect.get("kind") == "order"), None)
        if order is not None:
            pending = order

        # 3. Mark to market at this bar's close.
        if book.quantity > 0:
            bars_in_position += 1
            book.peak = bar.close if book.peak is None else max(book.peak, bar.close)
        position_value = book.quantity * bar.close
        equity_curve.append(
            EquityPoint(
                time=now,
                price_usd=bar.close,
                cash_usd=book.cash,
                quantity=book.quantity,
                position_value_usd=position_value,
                equity_usd=book.cash + position_value,
            )
        )

    final_price = bars[-1].close if bars else ZERO
    final_position_value = book.quantity * final_price
    final_equity = book.cash + final_position_value
    metrics = _metrics(
        equity_curve=equity_curve,
        trades=trades,
        starting_cash=starting_cash_usd,
        final_equity=final_equity,
        bars=len(bars),
        bars_in_position=bars_in_position,
        fees_paid=fees_paid,
    )

    return BacktestResult(
        primary_timeframe=primary_timeframe,
        starting_cash_usd=starting_cash_usd,
        final_cash_usd=book.cash,
        final_quantity=book.quantity,
        final_equity_usd=final_equity,
        equity_curve=tuple(equity_curve),
        trades=tuple(trades),
        metrics=metrics,
        node_hits=node_hits,
        warmup_bars=dict(report.warmup_bars),
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
    )


def _apply_set_vars(decision: GraphDecision, variables: dict[str, Decimal | None]) -> None:
    """Variables are written after the tick is recorded (contract section 4.4)."""
    for effect in decision.effects:
        if effect.get("kind") != "set_var":
            continue
        name = effect.get("name")
        value = effect.get("value")
        if isinstance(name, str) and isinstance(value, Decimal):
            variables[name] = value


def _execute(
    *,
    book: _Book,
    effect: Mapping[str, Any],
    open_price: Decimal,
    when: datetime,
    fee_rate: Decimal,
    slippage_rate: Decimal,
) -> BacktestTrade | None:
    """Fill one order at ``open_price`` with slippage and fees. Returns the fill."""
    side = effect.get("side")
    node = effect.get("node")
    node_id = node if isinstance(node, str) else None
    with localcontext() as context:
        context.prec = 28
        if side == "buy":
            amount = effect.get("amount_usd")
            if not isinstance(amount, Decimal) or amount <= 0:
                return None
            price = open_price * (1 + slippage_rate)
            if price <= 0:
                return None
            affordable = book.cash / (1 + fee_rate) if fee_rate > 0 else book.cash
            amount = min(amount, affordable)
            if amount <= 0:
                return None
            fee = amount * fee_rate
            quantity = amount / price
            book.cash -= amount + fee
            book.quantity += quantity
            book.basis += amount + fee
            if book.entry_at is None:
                book.entry_at = when
            book.peak = price if book.peak is None else max(book.peak, price)
            return BacktestTrade(
                time=when,
                side="buy",
                price_usd=price,
                quantity=quantity,
                notional_usd=amount,
                fee_usd=fee,
                pnl_usd=None,
                node=node_id,
            )

        fraction = effect.get("sell_fraction")
        if not isinstance(fraction, Decimal) or fraction <= 0 or book.quantity <= 0:
            return None
        fraction = min(fraction, Decimal(1))
        price = open_price * (1 - slippage_rate)
        if price <= 0:
            return None
        quantity = book.quantity * fraction
        notional = quantity * price
        fee = notional * fee_rate
        average = book.average_cost()
        basis_removed = ZERO if average is None else average * quantity
        pnl = notional - fee - basis_removed
        book.cash += notional - fee
        book.quantity -= quantity
        book.basis -= basis_removed
        if book.quantity <= 0:
            book.quantity = ZERO
            book.basis = ZERO
            book.entry_at = None
            book.peak = None
        else:
            book.peak = price if book.peak is None else max(book.peak, price)
        return BacktestTrade(
            time=when,
            side="sell",
            price_usd=price,
            quantity=quantity,
            notional_usd=notional,
            fee_usd=fee,
            pnl_usd=pnl,
            node=node_id,
        )


def _max_drawdown_pct(equity_curve: Sequence[EquityPoint]) -> Decimal:
    peak: Decimal | None = None
    worst = ZERO
    for point in equity_curve:
        equity = point.equity_usd
        if peak is None or equity > peak:
            peak = equity
            continue
        if peak > 0:
            drawdown = (peak - equity) / peak * HUNDRED
            worst = max(worst, drawdown)
    return _pct(worst)


def _sharpe(equity_curve: Sequence[EquityPoint]) -> Decimal | None:
    """Mean/stdev of per-bar equity returns. ``None`` when undefined (< 2 returns, no
    dispersion). Not annualised: the metric is per bar, on the timeframe's bars."""
    returns: list[Decimal] = []
    with localcontext() as context:
        context.prec = 28
        for previous, current in zip(equity_curve, equity_curve[1:], strict=False):
            if previous.equity_usd <= 0:
                continue
            returns.append(current.equity_usd / previous.equity_usd - 1)
        if len(returns) < 2:
            return None
        count = Decimal(len(returns))
        mean = sum(returns, ZERO) / count
        variance = sum(((value - mean) ** 2 for value in returns), ZERO) / count
        if variance <= 0:
            return None
        deviation = variance.sqrt()
        if deviation == 0:
            return None
        return _pct(mean / deviation)


def _metrics(
    *,
    equity_curve: Sequence[EquityPoint],
    trades: Sequence[BacktestTrade],
    starting_cash: Decimal,
    final_equity: Decimal,
    bars: int,
    bars_in_position: int,
    fees_paid: Decimal,
) -> BacktestMetrics:
    closed = [trade for trade in trades if trade.pnl_usd is not None]
    wins = [trade for trade in closed if trade.pnl_usd is not None and trade.pnl_usd > 0]
    gross_profit = sum(
        (trade.pnl_usd for trade in closed if trade.pnl_usd is not None and trade.pnl_usd > 0), ZERO
    )
    gross_loss = -sum(
        (trade.pnl_usd for trade in closed if trade.pnl_usd is not None and trade.pnl_usd < 0), ZERO
    )
    net_pnl = final_equity - starting_cash
    with localcontext() as context:
        context.prec = 28
        return_pct = net_pnl / starting_cash * HUNDRED
        win_rate = Decimal(len(wins)) / Decimal(len(closed)) * HUNDRED if closed else None
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
        exposure = Decimal(bars_in_position) / Decimal(bars) * HUNDRED if bars else ZERO

    return BacktestMetrics(
        net_pnl_usd=_money(net_pnl),
        return_pct=_pct(return_pct),
        max_drawdown_pct=_max_drawdown_pct(equity_curve),
        win_rate_pct=None if win_rate is None else _pct(win_rate),
        profit_factor=None if profit_factor is None else _pct(profit_factor),
        trades=len(trades),
        closed_trades=len(closed),
        exposure_pct=_pct(exposure),
        sharpe=_sharpe(equity_curve),
        fees_usd=_money(fees_paid),
    )


__all__ = [
    "BACKTEST_LABEL",
    "DATA_SOURCE",
    "FILL_MODEL",
    "BacktestMetrics",
    "BacktestResult",
    "BacktestTrade",
    "EquityPoint",
    "run_backtest",
]
