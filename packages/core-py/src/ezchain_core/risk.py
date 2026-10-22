"""Fail-closed paper risk policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from decimal import Decimal

from .types import OrderRequest, Quote, Side, SourceRef, TokenSnapshot


@dataclass(frozen=True)
class RiskLimits:
    max_order_usd: Decimal = Decimal("1000")
    max_position_usd: Decimal = Decimal("2500")
    max_open_positions: int = 10
    max_daily_loss_usd: Decimal = Decimal("500")
    max_quote_age_seconds: float = 60.0
    min_liquidity_usd: Decimal = Decimal("10000")
    min_pair_age_minutes: int = 10
    paper_mark_max_order_usd: Decimal = Decimal("250")
    # Execution-cost ceiling for new risk, as a ratio (0.05 = 5%). Only an executable
    # quote reports an impact; a ``paper_mark`` reports none and is never gated by this.
    max_price_impact_pct: Decimal = Decimal("0.05")


def evaluate_order(
    *,
    request: OrderRequest,
    snapshot: TokenSnapshot,
    quote: Quote,
    current_position_value_usd: Decimal,
    open_positions: int,
    daily_realized_pnl_usd: Decimal,
    limits: RiskLimits | None = None,
    now_source: SourceRef | None = None,
) -> tuple[bool, tuple[str, ...]]:
    """Return (allowed, reasons). Unknown or missing data always denies."""
    limits = limits or RiskLimits()
    reasons: list[str] = []

    if request.side not in ("buy", "sell"):
        reasons.append("INVALID_SIDE")
    if request.amount_usd <= 0:
        reasons.append("INVALID_AMOUNT")
    # Size, paper-mark and daily-loss limits cap new risk. They must never block a sell:
    # a position larger than max_order_usd still has to be exitable, and a daily-loss
    # breach is exactly when reducing exposure matters most. Fail-closed data gates
    # (price, liquidity, route, freshness) still apply to sells below. Sell quantity is
    # bounded to the held position by the order service; this pure policy does not carry
    # a position quantity and must not invent a second cap.
    if request.side == "buy" and request.amount_usd > limits.max_order_usd:
        reasons.append("ORDER_LIMIT")
    if (
        request.side == "buy"
        and quote.kind == "paper_mark"
        and request.amount_usd > limits.paper_mark_max_order_usd
    ):
        reasons.append("PAPER_MARK_LIMIT")
    # Impact gates new risk only. Refusing an exit because the exit is expensive is how a
    # paper position gets trapped, so a sell records the impact but is never blocked by it.
    # An unknown impact (``paper_mark``) is not a breach: the mark is not an executable route.
    if (
        request.side == "buy"
        and quote.price_impact_pct is not None
        and abs(quote.price_impact_pct) > limits.max_price_impact_pct
    ):
        reasons.append("PRICE_IMPACT_TOO_HIGH")

    source = now_source or quote.source
    if source.is_stale(limits.max_quote_age_seconds):
        reasons.append("QUOTE_STALE")

    if snapshot.price_usd is None:
        reasons.append("PRICE_UNKNOWN")
    if snapshot.liquidity_usd is None:
        reasons.append("LIQUIDITY_UNKNOWN")
    elif snapshot.liquidity_usd < limits.min_liquidity_usd:
        reasons.append("LIQUIDITY_TOO_LOW")

    # Pair age sizes new risk. A missing creation time is unknown, never "old"; sells
    # stay exempt because reducing exposure must remain possible.
    if request.side == "buy":
        if snapshot.pair_created_at is None:
            reasons.append("PAIR_AGE_UNKNOWN")
        else:
            created = (
                snapshot.pair_created_at.astimezone(UTC)
                if snapshot.pair_created_at.tzinfo is not None
                else snapshot.pair_created_at.replace(tzinfo=UTC)
            )
            observed = (
                source.observed_at.astimezone(UTC)
                if source.observed_at.tzinfo is not None
                else source.observed_at.replace(tzinfo=UTC)
            )
            age_minutes = (observed - created).total_seconds() / 60
            if age_minutes < limits.min_pair_age_minutes:
                reasons.append("PAIR_TOO_YOUNG")

    if not snapshot.pairs:
        reasons.append("NO_PAIR")
    if request.side == "sell" and quote.amount_out <= 0:
        reasons.append("NO_SELL_ROUTE")
    if snapshot.stale:
        reasons.append("SNAPSHOT_STALE")

    # Position-size limits gate opening or adding risk, never reducing it: a sell must
    # always be allowed when the only "problem" is that the position is large.
    if request.side == "buy":
        if current_position_value_usd + request.amount_usd > limits.max_position_usd:
            reasons.append("POSITION_LIMIT")
        if open_positions >= limits.max_open_positions and current_position_value_usd == 0:
            reasons.append("MAX_OPEN_POSITIONS")
    if request.side == "buy" and daily_realized_pnl_usd <= -limits.max_daily_loss_usd:
        reasons.append("DAILY_LOSS_LIMIT")

    return (not reasons, tuple(reasons))


def side_from_quote(quote: Quote) -> Side:
    return quote.side
