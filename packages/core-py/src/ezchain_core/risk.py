"""Fail-closed paper risk policy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC
from decimal import Decimal

from .types import OrderRequest, Quote, Side, SourceRef, TokenSnapshot

# The profile names are part of the API contract and the persisted settings payload.
PROFILE_NAMES: tuple[str, ...] = ("low", "mid", "high")
HUNDRED = Decimal(100)


@dataclass(frozen=True)
class TradeSizeLimits:
    """One absolute + percentage-of-equity size envelope.

    ``max_*`` is optional so a pure policy can express "no ceiling", but the deployed
    policy always supplies one. Every value is a Decimal; nothing here quantizes to
    cents. Quantization belongs at the order boundary.
    """

    min_amount_usd: Decimal = Decimal(0)
    min_pct_equity: Decimal = Decimal(0)
    max_amount_usd: Decimal | None = None
    max_pct_equity: Decimal | None = None


@dataclass(frozen=True)
class TradeSizePolicy:
    """Workspace size envelope plus the low/mid/high profiles.

    ``limits`` is the outer envelope: a profile may only tighten it. The resolver
    intersects the two, so a draft or hand-constructed policy cannot size an order
    larger than the workspace cap even when validation was bypassed.
    """

    limits: TradeSizeLimits
    profiles: Mapping[str, TradeSizeLimits]
    default_profile: str = "mid"


@dataclass(frozen=True)
class TradeSizeResolution:
    """A resolved size or a stable refusal reason. Never a silent fallback.

    ``amount_usd`` is unquantized; callers that create an order quantize once to cents
    and must not resize the result again.
    """

    amount_usd: Decimal | None
    profile: str | None
    limits: TradeSizeLimits | None
    reason: str | None = None
    equity_usd: Decimal | None = None
    manual: bool = False

    @property
    def ok(self) -> bool:
        return self.reason is None and self.amount_usd is not None


def _has_non_finite(limits: TradeSizeLimits) -> bool:
    return any(
        value is not None and not value.is_finite()
        for value in (
            limits.min_amount_usd,
            limits.min_pct_equity,
            limits.max_amount_usd,
            limits.max_pct_equity,
        )
    )


def _limit_errors(limits: TradeSizeLimits) -> list[str]:
    reasons: list[str] = []
    if _has_non_finite(limits):
        # Decimal comparisons with NaN raise; refuse before any bound check.
        return ["NOT_FINITE"]
    if limits.min_amount_usd < 0:
        reasons.append("AMOUNT_NEGATIVE")
    if limits.max_amount_usd is not None and limits.max_amount_usd < 0:
        reasons.append("AMOUNT_NEGATIVE")
    if not (Decimal(0) <= limits.min_pct_equity <= HUNDRED):
        reasons.append("PCT_OUT_OF_RANGE")
    if limits.max_pct_equity is not None and not (Decimal(0) <= limits.max_pct_equity <= HUNDRED):
        reasons.append("PCT_OUT_OF_RANGE")
    if limits.max_amount_usd is not None and limits.min_amount_usd > limits.max_amount_usd:
        reasons.append("MIN_ABOVE_MAX")
    if limits.max_pct_equity is not None and limits.min_pct_equity > limits.max_pct_equity:
        reasons.append("MIN_ABOVE_MAX")
    return reasons


def validate_trade_policy(policy: TradeSizePolicy) -> tuple[str, ...]:
    """Return stable validation codes; an empty tuple means the policy is usable.

    The workspace envelope is a hard outer bound: every profile must sit inside it.
    This is what makes the four top-level fields meaningful rather than decorative.
    """
    reasons: list[str] = []
    every_limit = [policy.limits, *policy.profiles.values()]
    if any(_has_non_finite(limits) for limits in every_limit):
        return ("NOT_FINITE",)
    reasons.extend(_limit_errors(policy.limits))
    names = set(policy.profiles)
    if set(PROFILE_NAMES) - names:
        reasons.append("PROFILE_UNKNOWN")
    if names - set(PROFILE_NAMES):
        reasons.append("PROFILE_UNKNOWN")
    if policy.default_profile not in PROFILE_NAMES or policy.default_profile not in names:
        reasons.append("PROFILE_UNKNOWN")

    outer = policy.limits
    for name in PROFILE_NAMES:
        limits = policy.profiles.get(name)
        if limits is None:
            continue
        reasons.extend(_limit_errors(limits))
        if limits.min_amount_usd < outer.min_amount_usd:
            reasons.append("PROFILE_OUTSIDE_ENVELOPE")
        if (
            outer.max_amount_usd is not None
            and limits.max_amount_usd is not None
            and limits.max_amount_usd > outer.max_amount_usd
        ):
            reasons.append("PROFILE_OUTSIDE_ENVELOPE")
        if limits.min_pct_equity < outer.min_pct_equity:
            reasons.append("PROFILE_OUTSIDE_ENVELOPE")
        if (
            outer.max_pct_equity is not None
            and limits.max_pct_equity is not None
            and limits.max_pct_equity > outer.max_pct_equity
        ):
            reasons.append("PROFILE_OUTSIDE_ENVELOPE")
    return tuple(dict.fromkeys(reasons))


def _tightest_max(*values: Decimal | None) -> Decimal | None:
    present = [value for value in values if value is not None]
    return min(present) if present else None


def effective_trade_size_limits(policy: TradeSizePolicy, profile: str) -> TradeSizeLimits | None:
    """The profile intersected with the workspace envelope; ``None`` if unknown."""
    if profile not in PROFILE_NAMES:
        return None
    chosen = policy.profiles.get(profile)
    if chosen is None:
        return None
    outer = policy.limits
    return TradeSizeLimits(
        min_amount_usd=max(outer.min_amount_usd, chosen.min_amount_usd),
        min_pct_equity=max(outer.min_pct_equity, chosen.min_pct_equity),
        max_amount_usd=_tightest_max(outer.max_amount_usd, chosen.max_amount_usd),
        max_pct_equity=_tightest_max(outer.max_pct_equity, chosen.max_pct_equity),
    )


def _pct_of(equity_usd: Decimal, pct: Decimal) -> Decimal:
    return equity_usd * pct / HUNDRED


def resolve_trade_size(
    *,
    equity_usd: Decimal | None,
    policy: TradeSizePolicy,
    profile: str | None = None,
    manual_amount_usd: Decimal | None = None,
) -> TradeSizeResolution:
    """Resolve a server-derived buy size, or refuse with a stable reason.

    Rules:
    * a manual amount is validated, never resized;
    * an automatic target is ``min(max_amount, max_pct% * equity)`` and must also satisfy
      ``min_amount`` / ``min_pct% * equity``. If equity cannot reach the minimum without
      breaching a maximum, the resolver refuses ``SIZE_BELOW_MIN`` instead of silently
      upsizing past the maximum;
    * unknown equity is ``EQUITY_UNKNOWN`` whenever a percentage bound is in play;
    * an unknown profile is ``PROFILE_UNKNOWN``;
    * no branch invents a default amount.
    """
    chosen_profile = profile or policy.default_profile
    chosen_limits = policy.profiles.get(chosen_profile) if chosen_profile in PROFILE_NAMES else None
    if chosen_limits is None:
        return TradeSizeResolution(
            amount_usd=None,
            profile=None,
            limits=None,
            reason="PROFILE_UNKNOWN",
            equity_usd=equity_usd,
            manual=manual_amount_usd is not None,
        )
    if _has_non_finite(policy.limits) or _has_non_finite(chosen_limits):
        return TradeSizeResolution(
            amount_usd=None,
            profile=chosen_profile,
            limits=None,
            reason="NOT_FINITE",
            equity_usd=equity_usd,
            manual=manual_amount_usd is not None,
        )
    limits = effective_trade_size_limits(policy, chosen_profile)
    if limits is None:
        return TradeSizeResolution(
            amount_usd=None,
            profile=None,
            limits=None,
            reason="PROFILE_UNKNOWN",
            equity_usd=equity_usd,
            manual=manual_amount_usd is not None,
        )

    errors = _limit_errors(limits)
    if errors:
        return TradeSizeResolution(
            amount_usd=None,
            profile=chosen_profile,
            limits=limits,
            reason=errors[0],
            equity_usd=equity_usd,
            manual=manual_amount_usd is not None,
        )

    uses_equity = limits.min_pct_equity > 0 or limits.max_pct_equity is not None
    if equity_usd is None and uses_equity:
        return TradeSizeResolution(
            amount_usd=None,
            profile=chosen_profile,
            limits=limits,
            reason="EQUITY_UNKNOWN",
            equity_usd=None,
            manual=manual_amount_usd is not None,
        )

    equity = Decimal(0) if equity_usd is None else equity_usd
    lower = max(limits.min_amount_usd, _pct_of(equity, limits.min_pct_equity))
    upper = _tightest_max(
        limits.max_amount_usd,
        None if limits.max_pct_equity is None else _pct_of(equity, limits.max_pct_equity),
    )
    target = lower if upper is None else upper

    if manual_amount_usd is not None:
        manual = manual_amount_usd
        if not manual.is_finite() or manual <= 0 or manual < lower:
            return TradeSizeResolution(
                amount_usd=None,
                profile=chosen_profile,
                limits=limits,
                reason="SIZE_BELOW_MIN",
                equity_usd=equity_usd,
                manual=True,
            )
        if upper is not None and manual > upper:
            return TradeSizeResolution(
                amount_usd=None,
                profile=chosen_profile,
                limits=limits,
                reason="SIZE_ABOVE_MAX",
                equity_usd=equity_usd,
                manual=True,
            )
        return TradeSizeResolution(
            amount_usd=manual,
            profile=chosen_profile,
            limits=limits,
            reason=None,
            equity_usd=equity_usd,
            manual=True,
        )

    if target <= 0 or target < lower:
        return TradeSizeResolution(
            amount_usd=None,
            profile=chosen_profile,
            limits=limits,
            reason="SIZE_BELOW_MIN",
            equity_usd=equity_usd,
            manual=False,
        )
    if limits.max_amount_usd is not None and target > limits.max_amount_usd:
        return TradeSizeResolution(
            amount_usd=None,
            profile=chosen_profile,
            limits=limits,
            reason="SIZE_ABOVE_MAX",
            equity_usd=equity_usd,
            manual=False,
        )
    return TradeSizeResolution(
        amount_usd=target,
        profile=chosen_profile,
        limits=limits,
        reason=None,
        equity_usd=equity_usd,
        manual=False,
    )


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
