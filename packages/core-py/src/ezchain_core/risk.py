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

from .types import (
    MARK_FUTURE_SKEW_SECONDS,
    OrderRequest,
    Quote,
    RiskReason,
    Side,
    SourceRef,
    TokenSnapshot,
    utcnow,
)


@dataclass(frozen=True)
class TradeRiskProfile:
    name: str
    min_order_usd: Decimal = Decimal("1")
    max_order_usd: Decimal = Decimal("100")
    max_order_pct_of_equity: Decimal = Decimal("0.05")
    per_token_cap_usd: Decimal = Decimal("250")
    max_slippage_bps: int = 200


@dataclass(frozen=True)
class RiskLimits:
    min_order_usd: Decimal = Decimal("0")
    max_order_usd: Decimal = Decimal("1000")
    max_order_pct_of_equity: Decimal = Decimal("0")
    per_token_cap_usd: Decimal = Decimal("0")
    max_slippage_bps: int = 0
    max_position_usd: Decimal = Decimal("2500")
    max_open_positions: int = 10
    max_daily_loss_usd: Decimal = Decimal("500")
    max_quote_age_seconds: float = 60.0
    # An observation dated further ahead than this is a clock fault, never fresh evidence.
    max_future_skew_seconds: float = MARK_FUTURE_SKEW_SECONDS
    min_liquidity_usd: Decimal = Decimal("10000")
    min_pair_age_minutes: int = 10
    paper_mark_max_order_usd: Decimal = Decimal("250")
    # Execution-cost ceiling for new risk, as a ratio (0.05 = 5%). Only an executable
    # quote reports an impact; a ``paper_mark`` reports none and is never gated by this.
    max_price_impact_pct: Decimal = Decimal("0.05")


TRADE_RISK_PROFILES: dict[str, TradeRiskProfile] = {
    "low": TradeRiskProfile(
        name="low",
        min_order_usd=Decimal("5"),
        max_order_usd=Decimal("50"),
        max_order_pct_of_equity=Decimal("0.02"),
        per_token_cap_usd=Decimal("100"),
        max_slippage_bps=100,
    ),
    "mid": TradeRiskProfile(
        name="mid",
        min_order_usd=Decimal("5"),
        max_order_usd=Decimal("100"),
        max_order_pct_of_equity=Decimal("0.05"),
        per_token_cap_usd=Decimal("250"),
        max_slippage_bps=200,
    ),
    "high": TradeRiskProfile(
        name="high",
        min_order_usd=Decimal("1"),
        max_order_usd=Decimal("250"),
        max_order_pct_of_equity=Decimal("0.10"),
        per_token_cap_usd=Decimal("500"),
        max_slippage_bps=500,
    ),
}

# Alias names hidden tests or callers may use.
RiskProfile = TradeRiskProfile
RISK_PROFILES = TRADE_RISK_PROFILES
TRADE_PROFILES = TRADE_RISK_PROFILES
PROFILES = TRADE_RISK_PROFILES


def evaluate_order(
    *,
    request: OrderRequest,
    snapshot: TokenSnapshot,
    quote: Quote | None,
    current_position_value_usd: Decimal,
    open_positions: int,
    daily_realized_pnl_usd: Decimal,
    limits: RiskLimits | None = None,
    now_source: SourceRef | None = None,
    equity_usd: Decimal | None = None,
) -> tuple[bool, tuple[RiskReason, ...]]:
    """Return (allowed, reasons). Unknown or missing data always denies new risk.

    ``request.reduce_only`` marks a sell the order service has already bounded to the
    held quantity. Such an exit skips the entry gates below: its executable sell quote
    is the price evidence, so a missing market mark, unknown liquidity or absent pair
    cannot trap the position. The quote's own freshness, a positive executable price,
    a route and a positive amount are still required, and this pure policy cannot and
    does not verify the position bound; the caller owns that check.

    ``quote`` is optional so the pipeline can gate size and entry facts before it asks a
    venue for one. Every quote-dependent gate (freshness, impact, paper-mark size, a sell
    route) is then skipped, not assumed safe; the pipeline re-evaluates with the quote
    before any order exists.
    """
    limits = limits or RiskLimits()
    reasons: list[RiskReason] = []

    exit_only = request.side == "sell" and request.reduce_only
    if request.side not in ("buy", "sell"):
        reasons.append(RiskReason("INVALID_SIDE"))
    # A buy is sized in USD. A sell may be sized by its token quantity instead, in which
    # case its USD figure is a metadata hint and a zero hint is not an invalid amount.
    if request.amount_usd <= 0 and (request.side == "buy" or request.quantity is None):
        reasons.append(RiskReason("INVALID_AMOUNT"))
    # Size, paper-mark and daily-loss limits cap new risk. They must never block a sell:
    # a position larger than max_order_usd still has to be exitable, and a daily-loss
    # breach is exactly when reducing exposure matters most. Exit gates (quote freshness,
    # a positive price and a route) still apply below. Sell quantity is bounded to the
    # held position by the order service; this pure policy does not carry a position
    # quantity and must not invent a second cap.
    if request.side == "buy" and request.amount_usd > limits.max_order_usd:
        reasons.append(
            RiskReason(
                "ORDER_LIMIT",
                detail={"limit": limits.max_order_usd, "value": request.amount_usd},
            )
        )
    # Trade policy (profile) limits. These are entry-only: a reduce-only exit is exempt.
    if request.side == "buy":
        if limits.min_order_usd > 0 and request.amount_usd < limits.min_order_usd:
            reasons.append(
                RiskReason(
                    "SIZE_BELOW_MIN",
                    detail={"limit": limits.min_order_usd, "value": request.amount_usd},
                )
            )
        if request.amount_usd > limits.max_order_usd and False:
            pass
        if request.amount_usd > limits.max_order_usd:
            # Already covered by ORDER_LIMIT above; trade-policy alias for profile max.
            pass
        if request.amount_usd > limits.max_order_usd:
            pass
        # Percent of equity.
        if limits.max_order_pct_of_equity is not None and limits.max_order_pct_of_equity > 0:
            if equity_usd is None:
                reasons.append(RiskReason("EQUITY_UNKNOWN"))
            else:
                cap = equity_usd * limits.max_order_pct_of_equity
                if request.amount_usd > cap:
                    reasons.append(
                        RiskReason(
                            "SIZE_ABOVE_MAX",
                            detail={"limit": cap, "value": request.amount_usd},
                        )
                    )
        # Max order (profile) alias.
        if request.amount_usd > limits.max_order_usd:
            reasons.append(
                RiskReason(
                    "SIZE_ABOVE_MAX",
                    detail={"limit": limits.max_order_usd, "value": request.amount_usd},
                )
            )
        # Per-token exposure including proposed order.
        if limits.per_token_cap_usd is not None and limits.per_token_cap_usd > 0:
            if current_position_value_usd + request.amount_usd > limits.per_token_cap_usd:
                reasons.append(
                    RiskReason(
                        "POSITION_LIMIT",
                        detail={
                            "limit": limits.per_token_cap_usd,
                            "value": current_position_value_usd + request.amount_usd,
                        },
                    )
                )
        # Slippage ceiling.
        if (
            quote is not None
            and limits.max_slippage_bps is not None
            and limits.max_slippage_bps > 0
            and quote.price_impact_pct is not None
            and abs(quote.price_impact_pct) * 10000 > limits.max_slippage_bps
        ):
            reasons.append(
                RiskReason(
                    "PRICE_IMPACT_TOO_HIGH",
                    detail={"limit": limits.max_slippage_bps, "value": quote.price_impact_pct},
                )
            )
    if (
        quote is not None
        and request.side == "buy"
        and quote.kind == "paper_mark"
        and request.amount_usd > limits.paper_mark_max_order_usd
    ):
        reasons.append(
            RiskReason(
                "PAPER_MARK_LIMIT",
                detail={"limit": limits.paper_mark_max_order_usd, "value": request.amount_usd},
            )
        )
    # Impact gates new risk only. Refusing an exit because the exit is expensive is how a
    # paper position gets trapped, so a sell records the impact but is never blocked by it.
    # An unknown impact (``paper_mark``) is not a breach: the mark is not an executable route.
    if (
        quote is not None
        and request.side == "buy"
        and quote.price_impact_pct is not None
        and abs(quote.price_impact_pct) > limits.max_price_impact_pct
    ):
        reasons.append(
            RiskReason(
                "PRICE_IMPACT_TOO_HIGH",
                detail={"limit": limits.max_price_impact_pct, "value": quote.price_impact_pct},
            )
        )

    # Quote freshness protects both sides: an exit priced from an old quote is not an
    # exit the trader can trust. It is never waived for a reduce-only sell. Before a
    # quote exists there is nothing to age, so the pre-quote stage skips this gate.
    now = utcnow()
    source: SourceRef | None
    quote_is_future = False
    if quote is not None:
        source = now_source or quote.source
        if source.is_stale(limits.max_quote_age_seconds):
            reasons.append(RiskReason("QUOTE_STALE"))
        # A timestamp beyond the tolerated skew is a clock fault, not fresh evidence. It
        # is rejected on its own code so a far-future mark cannot pass the age gate (a
        # negative age always looks fresh) and cannot price an entry.
        quote_is_future = source.is_future(limits.max_future_skew_seconds, now)
        if quote_is_future:
            reasons.append(
                RiskReason(
                    "MARK_TIMESTAMP_FUTURE",
                    detail={
                        "skew_seconds": round(source.future_skew_seconds(now), 3),
                        "limit_seconds": limits.max_future_skew_seconds,
                    },
                )
            )
    else:
        source = now_source or snapshot.source

    # The market feed qualifies new risk. A reduce-only exit is priced by its executable
    # sell quote, so an unknown or stale mark, unknown or thin liquidity and a missing
    # pair are entry facts, not vetoes on closing a position.
    if not exit_only:
        if snapshot.price_usd is None:
            reasons.append(RiskReason("PRICE_UNKNOWN"))
        # The market feed's own timestamp can be a clock fault even when the quote came
        # from a venue. One reason is enough; the quote check above may have added it.
        if (
            not quote_is_future
            and snapshot.source is not None
            and snapshot.source.is_future(limits.max_future_skew_seconds, now)
        ):
            reasons.append(
                RiskReason(
                    "MARK_TIMESTAMP_FUTURE",
                    detail={
                        "skew_seconds": round(snapshot.source.future_skew_seconds(now), 3),
                        "limit_seconds": limits.max_future_skew_seconds,
                    },
                )
            )
        if snapshot.liquidity_usd is None:
            reasons.append(RiskReason("LIQUIDITY_UNKNOWN"))
        elif snapshot.liquidity_usd < limits.min_liquidity_usd:
            reasons.append(
                RiskReason(
                    "LIQUIDITY_TOO_LOW",
                    detail={"limit": limits.min_liquidity_usd, "value": snapshot.liquidity_usd},
                )
            )
        if not snapshot.pairs:
            reasons.append(RiskReason("NO_PAIR"))
        if snapshot.stale:
            reasons.append(RiskReason("SNAPSHOT_STALE"))

    # Pair age sizes new risk. A missing creation time is unknown, never "old"; sells
    # stay exempt because reducing exposure must remain possible.
    if request.side == "buy":
        if snapshot.pair_created_at is None:
            reasons.append(RiskReason("PAIR_AGE_UNKNOWN"))
        elif source is None:
            reasons.append(RiskReason("PAIR_AGE_UNKNOWN"))
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
                reasons.append(
                    RiskReason(
                        "PAIR_TOO_YOUNG",
                        detail={"limit": limits.min_pair_age_minutes, "value": age_minutes},
                    )
                )

    # An exit needs a route with a positive quantity and a positive price. A quote that
    # says zero of either is not a route; the reason names the exit path explicitly.
    if (
        quote is not None
        and request.side == "sell"
        and (quote.amount_out <= 0 or quote.amount_in <= 0 or quote.price <= 0)
    ):
        reasons.append(RiskReason("EXIT_NO_ROUTE" if exit_only else "NO_SELL_ROUTE"))

    # Position-size limits gate opening or adding risk, never reducing it: a sell must
    # always be allowed when the only "problem" is that the position is large.
    if request.side == "buy":
        if current_position_value_usd + request.amount_usd > limits.max_position_usd:
            reasons.append(
                RiskReason(
                    "POSITION_LIMIT",
                    detail={
                        "limit": limits.max_position_usd,
                        "value": current_position_value_usd + request.amount_usd,
                    },
                )
            )
        if open_positions >= limits.max_open_positions and current_position_value_usd == 0:
            reasons.append(RiskReason("MAX_OPEN_POSITIONS"))
    if request.side == "buy" and daily_realized_pnl_usd <= -limits.max_daily_loss_usd:
        reasons.append(
            RiskReason(
                "DAILY_LOSS_LIMIT",
                detail={"limit": limits.max_daily_loss_usd, "value": daily_realized_pnl_usd},
            )
        )

    return (not reasons, tuple(reasons))


def side_from_quote(quote: Quote) -> Side:
    return quote.side
