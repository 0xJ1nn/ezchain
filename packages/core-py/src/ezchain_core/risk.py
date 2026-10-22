"""Fail-closed paper risk policy."""

from __future__ import annotations

from dataclasses import dataclass
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
    if request.amount_usd > limits.max_order_usd:
        reasons.append("ORDER_LIMIT")
    if quote.kind == "paper_mark" and request.amount_usd > limits.paper_mark_max_order_usd:
        reasons.append("PAPER_MARK_LIMIT")

    source = now_source or quote.source
    if source.is_stale(limits.max_quote_age_seconds):
        reasons.append("QUOTE_STALE")

    if snapshot.price_usd is None:
        reasons.append("PRICE_UNKNOWN")
    if snapshot.liquidity_usd is None:
        reasons.append("LIQUIDITY_UNKNOWN")
    elif snapshot.liquidity_usd < limits.min_liquidity_usd:
        reasons.append("LIQUIDITY_TOO_LOW")

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
    if daily_realized_pnl_usd <= -limits.max_daily_loss_usd:
        reasons.append("DAILY_LOSS_LIMIT")

    return (not reasons, tuple(reasons))


def side_from_quote(quote: Quote) -> Side:
    return quote.side
