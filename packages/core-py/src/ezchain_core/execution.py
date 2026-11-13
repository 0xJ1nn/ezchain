"""Typed stages of the single execution path.

``Intent -> RiskDecision -> Quote -> Order -> Fill -> Journal``. Paper, testnet and live
run these same objects; only the venue adapter and the custodian differ. Nothing here
performs I/O or writes the ledger: a runner (``services/executor``) drives the stages and
hands the finished :class:`Journal` to a writer that appends to ``ledger_entries``.
Unknowns stay unknown: no stage fabricates a zero to make a decision pass.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from .ledger import LedgerEntry, buy_entries, sell_entries
from .risk import RiskLimits, evaluate_order
from .types import (
    OrderRequest,
    PositionState,
    Quote,
    RiskDecision,
    RiskReason,
    Side,
    TokenSnapshot,
    utcnow,
)

if TYPE_CHECKING:
    from .signing import SignedTransaction

ExecutionStatus = Literal[
    "filled",
    "partially_filled",
    "rejected",
    "timeout",
    "denied",
    "journal_error",
]


@dataclass(frozen=True)
class Intent:
    """A request, never an order.

    ``idempotency_key`` is mandatory: the pipeline dedupes on it, so an intent without
    one could be submitted twice. A buy is sized in USD; a sell is sized by its token
    quantity or, for a reduce-only exit, by the position it closes. A sell may carry a
    zero USD hint when the caller knows the quantity and no mark exists.

    ``actor`` is the principal the order belongs to, resolved server-side (the
    authenticated caller, the agent key or the strategy attribution) and never a request
    field. It is what a custody path compares with the key handle's owner: the signer is
    handed an approval, so an order whose principal is only asserted by a caller could be
    signed with somebody else's key. ``automation`` names the rule that fired when nobody
    is in the loop; an unattended intent needs the consent record for that user.
    """

    side: Side
    amount_usd: Decimal
    chain: str
    token_address: str
    idempotency_key: str
    quantity: Decimal | None = None
    reduce_only: bool = False
    strategy_id: str | None = None
    risk_profile: str | None = None
    actor: str | None = None
    automation: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.side not in ("buy", "sell"):
            raise ValueError("side must be buy or sell")
        if not self.chain:
            raise ValueError("intent chain is required")
        if not self.token_address:
            raise ValueError("intent token address is required")
        if not self.idempotency_key:
            raise ValueError("intent idempotency key is required")
        if self.actor is not None and not self.actor:
            raise ValueError("an attributed intent names its principal")
        if self.automation is not None and not self.automation:
            raise ValueError("an unattended intent names the automation that fired")
        if self.amount_usd < 0:
            raise ValueError("intent amount cannot be negative")
        if self.quantity is not None and self.quantity <= 0:
            raise ValueError("intent quantity must be positive")
        if self.side == "buy" and self.amount_usd <= 0:
            raise ValueError("a buy must be sized in USD")
        if self.amount_usd <= 0 and self.quantity is None:
            raise ValueError("intent needs a positive amount or quantity")

    @property
    def key(self) -> str:
        return self.idempotency_key

    def sized(self, *, amount_usd: Decimal, quantity: Decimal | None) -> Intent:
        """The same intent with the size the pipeline resolved."""
        return replace(self, amount_usd=amount_usd, quantity=quantity)

    def to_order_request(
        self, *, amount_usd: Decimal | None = None, quantity: Decimal | None = None
    ) -> OrderRequest:
        return OrderRequest(
            chain=self.chain,
            token_address=self.token_address,
            side=self.side,
            amount_usd=self.amount_usd if amount_usd is None else amount_usd,
            idempotency_key=self.idempotency_key,
            strategy_id=self.strategy_id,
            quantity=self.quantity if quantity is None else quantity,
            reduce_only=self.reduce_only,
            risk_profile=self.risk_profile,
        )


@dataclass(frozen=True)
class Order:
    """A priced order: the quote's amounts, never the intent's request.

    ``actor`` is carried through from the intent so the approval built from this order
    names the principal whose key may sign it, not one a caller supplied at the seam.
    """

    id: str
    idempotency_key: str
    chain: str
    token_address: str
    side: Side
    amount_usd: Decimal
    quantity: Decimal
    quote: Quote
    risk_decision: RiskDecision
    reduce_only: bool = False
    actor: str | None = None
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if self.amount_usd <= 0:
            raise ValueError("order amount must be positive")
        if self.quantity <= 0:
            raise ValueError("order quantity must be positive")


@dataclass(frozen=True)
class Fill:
    """A confirmed venue receipt. The journal is built from these numbers alone."""

    order_id: str
    side: Side
    quantity: Decimal
    price_usd: Decimal
    notional_usd: Decimal
    fee_usd: Decimal
    status: Literal["filled", "partial", "rejected"] = "filled"
    receipt: Mapping[str, Any] = field(default_factory=dict)
    # The digest of the exact payload that was signed. A receipt for a signature this
    # process produced must echo it, so a venue cannot answer with a different fill.
    transaction_digest: str | None = None
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if self.quantity < 0 or self.notional_usd < 0 or self.price_usd < 0 or self.fee_usd < 0:
            raise ValueError("a fill cannot carry a negative amount")

    @property
    def is_partial(self) -> bool:
        return self.status == "partial"

    @property
    def bookable(self) -> bool:
        """A receipt the ledger may journal: accepted, sized and priced."""
        return (
            self.status != "rejected"
            and self.quantity > 0
            and self.notional_usd > 0
            and self.price_usd > 0
        )


@dataclass(frozen=True)
class Journal:
    """The ledger entries produced from one confirmed fill."""

    group_id: str
    entries: tuple[LedgerEntry, ...]
    fill: Fill
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class ExecutionResult:
    """Every stage the pipeline reached, and why it stopped there."""

    intent: Intent
    status: ExecutionStatus
    risk_decision: RiskDecision
    quote: Quote | None = None
    order: Order | None = None
    fill: Fill | None = None
    journal: Journal | None = None
    reason: str | None = None
    duplicate: bool = False


@runtime_checkable
class Venue(Protocol):
    """The only thing that differs between paper, testnet and live.

    ``submit`` receives the signed transaction when the executor signed one, so the venue
    can echo its digest back in the receipt and confirmation can prove it was the same
    payload.
    """

    async def quote(self, intent: Intent) -> Quote: ...

    async def submit(self, order: Order, signed: SignedTransaction | None = None) -> Fill: ...


def risk_decision_from_intent(
    *,
    intent: Intent,
    snapshot: TokenSnapshot | None = None,
    quote: Quote | None = None,
    position: PositionState | None = None,
    limits: RiskLimits | None = None,
    equity_usd: Decimal | None = None,
    open_positions: int = 0,
    daily_realized_pnl_usd: Decimal = Decimal(0),
) -> RiskDecision:
    """Evaluate the fail-closed policy for an intent.

    A buy with no market snapshot is denied: risk without evidence is unknown, never
    safe. A sell is an exit; the runner bounds its quantity to the held position and the
    quote stage still requires a fresh, executable route. ``quote`` is optional so the
    runner can gate entry facts before requesting one, then evaluate the quote's own
    freshness and impact before any order exists.
    """
    request = intent.to_order_request()
    if snapshot is None:
        # The pre-quote stage may pass a sell with no mark: an exit's executable quote is
        # its evidence and entry-only gates never block reducing risk. Once a quote
        # exists, though, the re-risk stage must not skip the quote's own gates, so a
        # missing snapshot fails closed for both sides.
        if quote is None and intent.side == "sell":
            return RiskDecision(allowed=True, reasons=())
        return RiskDecision(allowed=False, reasons=(RiskReason("MISSING_RISK_INPUTS"),))
    current_value = Decimal(0)
    if position is not None and snapshot.price_usd is not None:
        current_value = position.quantity * snapshot.price_usd
    allowed, reasons = evaluate_order(
        request=request,
        snapshot=snapshot,
        quote=quote,
        current_position_value_usd=current_value,
        open_positions=open_positions,
        daily_realized_pnl_usd=daily_realized_pnl_usd,
        limits=limits,
        equity_usd=equity_usd,
    )
    max_order_usd = None
    if allowed and limits is not None and not request.reduce_only:
        max_order_usd = limits.max_order_usd
    return RiskDecision(allowed=allowed, reasons=reasons, max_order_usd=max_order_usd)


def journal_from_fill(
    fill: Fill,
    *,
    chain: str,
    token_address: str,
    avg_cost_usd: Decimal = Decimal(0),
    cost_basis_usd: Decimal | None = None,
) -> Journal:
    """Build the ledger journal from the venue receipt, never from the intent."""
    if not fill.bookable:
        raise ValueError("a journal is built only from an accepted, sized fill")
    if fill.side == "buy":
        entries = buy_entries(
            chain=chain,
            token_address=token_address,
            quantity=fill.quantity,
            price_usd=fill.price_usd,
            fee_bps=0,
            notional_usd=fill.notional_usd,
            fee_usd=fill.fee_usd,
        )
    else:
        entries = sell_entries(
            chain=chain,
            token_address=token_address,
            quantity=fill.quantity,
            price_usd=fill.price_usd,
            avg_cost_usd=avg_cost_usd,
            fee_bps=0,
            proceeds_usd=fill.notional_usd,
            cost_basis_usd=cost_basis_usd,
            fee_usd=fill.fee_usd,
        )
    return Journal(group_id=f"fill:{fill.order_id}", entries=tuple(entries), fill=fill)


__all__ = [
    "ExecutionResult",
    "ExecutionStatus",
    "Fill",
    "Intent",
    "Journal",
    "Order",
    "Venue",
    "journal_from_fill",
    "risk_decision_from_intent",
]
