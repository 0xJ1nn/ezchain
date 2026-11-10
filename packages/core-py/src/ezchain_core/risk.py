"""Fail-closed paper risk policy.

Two kinds of gate live here. Entry gates decide whether new risk may be opened: order
size, position size, open-position count, daily loss, price impact, liquidity, pair age
and the presence of a market pair. Exit gates decide whether an existing position can
still be closed: a fresh quote, a positive executable price and a route that returns a
quantity. A reduce-only sell is an exit and is never blocked by an entry gate; an
unknown mark or a missing route still fails closed with a typed reason.
"""

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
    """Return (allowed, reasons). Unknown or missing data always denies new risk.

    ``request.reduce_only`` marks a sell the order service has already bounded to the
    held quantity. Such an exit skips the entry gates below: its executable sell quote
    is the price evidence, so a missing market mark, unknown liquidity or absent pair
    cannot trap the position. The quote's own freshness, a positive executable price,
    a route and a positive amount are still required, and this pure policy cannot and
    does not verify the position bound; the caller owns that check.
    """
    limits = limits or RiskLimits()
    reasons: list[str] = []

    exit_only = request.side == "sell" and request.reduce_only
    if request.side not in ("buy", "sell"):
        reasons.append("INVALID_SIDE")
    if request.amount_usd <= 0:
        reasons.append("INVALID_AMOUNT")
    # Size, paper-mark and daily-loss limits cap new risk. They must never block a sell:
    # a position larger than max_order_usd still has to be exitable, and a daily-loss
    # breach is exactly when reducing exposure matters most. Exit gates (quote freshness,
    # a positive price and a route) still apply below. Sell quantity is bounded to the
    # held position by the order service; this pure policy does not carry a position
    # quantity and must not invent a second cap.
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

    # Quote freshness protects both sides: an exit priced from an old quote is not an
    # exit the trader can trust. It is never waived for a reduce-only sell.
    source = now_source or quote.source
    if source.is_stale(limits.max_quote_age_seconds):
        reasons.append("QUOTE_STALE")

    # The market feed qualifies new risk. A reduce-only exit is priced by its executable
    # sell quote, so an unknown or stale mark, unknown or thin liquidity and a missing
    # pair are entry facts, not vetoes on closing a position.
    if not exit_only:
        if snapshot.price_usd is None:
            reasons.append("PRICE_UNKNOWN")
        if snapshot.liquidity_usd is None:
            reasons.append("LIQUIDITY_UNKNOWN")
        elif snapshot.liquidity_usd < limits.min_liquidity_usd:
            reasons.append("LIQUIDITY_TOO_LOW")
        if not snapshot.pairs:
            reasons.append("NO_PAIR")
        if snapshot.stale:
            reasons.append("SNAPSHOT_STALE")

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

    # An exit needs a route with a positive quantity and a positive price. A quote that
    # says zero of either is not a route; the reason names the exit path explicitly.
    if request.side == "sell" and (
        quote.amount_out <= 0 or quote.amount_in <= 0 or quote.price <= 0
    ):
        reasons.append("EXIT_NO_ROUTE" if exit_only else "NO_SELL_ROUTE")

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
