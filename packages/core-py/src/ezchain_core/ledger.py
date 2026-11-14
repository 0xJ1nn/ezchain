"""Append-only paper ledger.

Every entry carries both the asset amount and its USD value at the time of the fill.
For each order group, SUM(usd_value) must be zero **to the minor unit**; there is no
tolerance. Balances and positions are derived by replay; there is no mutable balance
scalar.

Money follows the policy in :mod:`ezchain_core.money` and ``docs/LEDGER.md``: USD is
written as a whole number of 1e-8 units, floats never cross the boundary, computed
values are rounded half-even once, and explicit sub-minor values fail closed. The
seal (:func:`seal_entries`) makes the tape tamper-evident and replay
(:func:`replay_balances`) is deterministic for the same tape.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from uuid import uuid4

from .money import (
    bps_fee,
    quantize_money,
    require_money,
    require_quantity,
    to_minor_units,
)

LEDGER_DIGEST_SCHEMA = "ezchain.ledger/v1"
LEDGER_GENESIS_DIGEST = "0" * 64
# Wide enough that summing a long tape never rounds a quantity or a cost basis.
_LEDGER_PRECISION = 60


class LedgerError(ValueError):
    """The tape violates a ledger invariant."""


class LedgerTamperedError(LedgerError):
    """A tape no longer matches the seal recorded for it."""


def _canonical_decimal(value: Decimal) -> str:
    """A stable, exponent-free form: ``1.5`` and ``1.50`` hash the same."""
    if value == 0:
        return "0"
    with localcontext() as context:
        context.prec = _LEDGER_PRECISION
        return format(value.normalize(), "f")


@dataclass(frozen=True)
class LedgerEntry:
    account: str
    asset: str
    amount: Decimal
    usd_value: Decimal
    kind: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", require_quantity(self.amount, field="amount"))
        object.__setattr__(self, "usd_value", require_money(self.usd_value, field="usd_value"))

    def to_row(self, group_id: str, created_at: datetime | None = None) -> dict[str, object]:
        return {
            "id": str(uuid4()),
            "group_id": group_id,
            "account": self.account,
            "asset": self.asset,
            "amount": self.amount,
            "usd_value": self.usd_value,
            "kind": self.kind,
            "created_at": created_at or datetime.now(UTC),
        }


@dataclass(frozen=True)
class AccountBalance:
    """One account's replay: token amount and exact USD minor units."""

    account: str
    amount: Decimal
    usd_minor: int


@dataclass(frozen=True)
class LedgerSeal:
    """Tamper-evidence for an ordered tape: its length and chained digest head."""

    count: int
    head: str


def cash_account() -> str:
    return "cash:USD"


def fee_account() -> str:
    return "fees:USD"


def realized_account() -> str:
    return "pnl:realized:USD"


def position_account(chain: str, token_address: str) -> str:
    return f"position:{chain}:{token_address}"


def genesis_entries(starting_cash_usd: Decimal) -> tuple[LedgerEntry, ...]:
    starting_cash_usd = require_money(starting_cash_usd, field="starting_cash_usd")
    if starting_cash_usd <= 0:
        raise ValueError("starting cash must be positive")
    return (
        LedgerEntry(cash_account(), "USD", starting_cash_usd, starting_cash_usd, "genesis"),
        LedgerEntry("equity:genesis", "USD", -starting_cash_usd, -starting_cash_usd, "genesis"),
    )


def buy_entries(
    *,
    chain: str,
    token_address: str,
    quantity: Decimal,
    price_usd: Decimal,
    fee_bps: int,
    notional_usd: Decimal | None = None,
    fee_usd: Decimal | None = None,
) -> tuple[LedgerEntry, ...]:
    """Journal for a buy: position +notional, cash -notional, fee expensed from cash.

    ``notional_usd`` is the quoted USD amount actually paid. It is passed explicitly when
    the implied ``quantity * price_usd`` cannot reproduce the quote exactly (a price is a
    ratio and may be non-terminating). The position leg then carries the exact cash cost.

    ``fee_usd`` is passed explicitly when the venue quote already carries a cost (pool or
    aggregator fee plus gas): the caller has resolved the bps-versus-quote maximum and the
    journal must book that exact number, not a second estimate of it.

    A derived notional is rounded once, half-even, to the ledger minor unit. An explicit
    notional or fee must already be a whole number of minor units or the call fails
    closed; the ledger never absorbs a sub-minor amount.
    """
    if quantity <= 0 or price_usd <= 0:
        raise ValueError("quantity and price must be positive")
    notional = (
        quantize_money(quantity * price_usd, field="notional_usd")
        if notional_usd is None
        else require_money(notional_usd, field="notional_usd")
    )
    if notional <= 0:
        raise ValueError("notional must be positive")
    fee = bps_fee(notional, fee_bps) if fee_usd is None else require_money(fee_usd, field="fee_usd")
    if fee < 0:
        raise ValueError("fee cannot be negative")
    entries = (
        LedgerEntry(
            position_account(chain, token_address),
            token_address,
            quantity,
            notional,
            "buy",
        ),
        LedgerEntry(cash_account(), "USD", -notional, -notional, "buy"),
        LedgerEntry(fee_account(), "USD", fee, fee, "fee"),
        LedgerEntry(cash_account(), "USD", -fee, -fee, "fee"),
    )
    assert_balanced(entries)
    return entries


def sell_entries(
    *,
    chain: str,
    token_address: str,
    quantity: Decimal,
    price_usd: Decimal,
    avg_cost_usd: Decimal,
    fee_bps: int,
    proceeds_usd: Decimal | None = None,
    cost_basis_usd: Decimal | None = None,
    fee_usd: Decimal | None = None,
) -> tuple[LedgerEntry, ...]:
    """Journal for a sell: cash +proceeds, position -cost basis, fee expensed from cash.

    Realized PnL is booked gross (proceeds - cost basis); the fee is its own expense leg,
    which keeps the group balanced. Read models report net realized by including fees.

    Derived proceeds and cost basis are rounded once, half-even, to the ledger minor
    unit; explicit values must already be whole minor units and fail closed otherwise.
    """
    if quantity <= 0 or price_usd <= 0:
        raise ValueError("quantity and price must be positive")
    if avg_cost_usd < 0:
        raise ValueError("average cost cannot be negative")
    proceeds = (
        quantize_money(quantity * price_usd, field="proceeds_usd")
        if proceeds_usd is None
        else require_money(proceeds_usd, field="proceeds_usd")
    )
    if proceeds <= 0:
        raise ValueError("proceeds must be positive")
    fee = bps_fee(proceeds, fee_bps) if fee_usd is None else require_money(fee_usd, field="fee_usd")
    if fee < 0:
        raise ValueError("fee cannot be negative")
    cost_basis = (
        quantize_money(quantity * avg_cost_usd, field="cost_basis_usd")
        if cost_basis_usd is None
        else require_money(cost_basis_usd, field="cost_basis_usd")
    )
    if cost_basis < 0:
        raise ValueError("cost basis cannot be negative")
    realized = proceeds - cost_basis
    entries = (
        LedgerEntry(cash_account(), "USD", proceeds, proceeds, "sell"),
        LedgerEntry(
            position_account(chain, token_address),
            token_address,
            -quantity,
            -cost_basis,
            "sell",
        ),
        LedgerEntry(cash_account(), "USD", -fee, -fee, "fee"),
        LedgerEntry(fee_account(), "USD", fee, fee, "fee"),
        LedgerEntry(realized_account(), "USD", -realized, -realized, "pnl"),
    )
    assert_balanced(entries)
    return entries


def assert_balanced(entries: Iterable[LedgerEntry]) -> None:
    """Every group must sum to exactly zero minor units. No tolerance is permitted."""
    total = 0
    for entry in entries:
        total += to_minor_units(entry.usd_value)
    if total != 0:
        raise LedgerError(f"ledger group does not balance: {total} minor units")


def _chain(head: str, index: int, entry: LedgerEntry) -> str:
    payload = "|".join(
        (
            LEDGER_DIGEST_SCHEMA,
            head,
            str(index),
            entry.account,
            entry.asset,
            _canonical_decimal(entry.amount),
            _canonical_decimal(entry.usd_value),
            entry.kind,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def ledger_digest(entries: Iterable[LedgerEntry], *, previous: str = LEDGER_GENESIS_DIGEST) -> str:
    """Chained SHA-256 over an ordered tape: any changed entry changes the head."""
    head = previous
    for index, entry in enumerate(entries, start=1):
        head = _chain(head, index, entry)
    return head


def seal_entries(entries: Iterable[LedgerEntry]) -> LedgerSeal:
    """Seal an ordered tape. The same tape always produces the same seal."""
    head = LEDGER_GENESIS_DIGEST
    count = 0
    for count, entry in enumerate(entries, start=1):
        head = _chain(head, count, entry)
    return LedgerSeal(count=count, head=head)


def verify_seal(entries: Iterable[LedgerEntry], seal: LedgerSeal) -> None:
    """Recompute the seal; raise :class:`LedgerTamperedError` on any difference."""
    actual = seal_entries(entries)
    if actual != seal:
        raise LedgerTamperedError(
            f"ledger seal mismatch: expected {seal.count} entries ending {seal.head}, "
            f"found {actual.count} ending {actual.head}"
        )


def replay_balances(entries: Iterable[LedgerEntry]) -> tuple[AccountBalance, ...]:
    """Project every account's amount and USD minor units from one ordered tape.

    Deterministic: the caller passes the tape in journal order and gets a canonical,
    account-sorted result. Amounts are summed at ledger precision so a long tape never
    rounds a quantity; USD is summed as integers, so there is nothing to drift.
    """
    amounts: dict[str, Decimal] = {}
    usd: dict[str, int] = {}
    with localcontext() as context:
        context.prec = _LEDGER_PRECISION
        for entry in entries:
            amounts[entry.account] = amounts.get(entry.account, Decimal(0)) + entry.amount
            usd[entry.account] = usd.get(entry.account, 0) + to_minor_units(entry.usd_value)
    return tuple(
        AccountBalance(account=account, amount=amounts[account], usd_minor=usd[account])
        for account in sorted(amounts)
    )


def replay_position(entries: Iterable[LedgerEntry]) -> tuple[Decimal, Decimal]:
    """Return (quantity, cost_basis_usd) for one position account.

    The caller must pass entries in ledger order (oldest first). Replay is intentionally
    order-dependent because a sell removes cost basis at the then-current average.
    """
    quantity = Decimal(0)
    cost = Decimal(0)
    with localcontext() as context:
        context.prec = _LEDGER_PRECISION
        for entry in entries:
            quantity += entry.amount
            cost += entry.usd_value
    if quantity < 0:
        raise ValueError("position quantity cannot be negative")
    if quantity == 0:
        cost = Decimal(0)
    return quantity, cost


def realized_pnl(entries: Iterable[LedgerEntry]) -> Decimal:
    with localcontext() as context:
        context.prec = _LEDGER_PRECISION
        return -sum(
            (entry.usd_value for entry in entries if entry.account == realized_account()),
            Decimal(0),
        )


__all__ = [
    "LEDGER_DIGEST_SCHEMA",
    "LEDGER_GENESIS_DIGEST",
    "AccountBalance",
    "LedgerEntry",
    "LedgerError",
    "LedgerSeal",
    "LedgerTamperedError",
    "assert_balanced",
    "buy_entries",
    "cash_account",
    "fee_account",
    "genesis_entries",
    "ledger_digest",
    "position_account",
    "realized_account",
    "realized_pnl",
    "replay_balances",
    "replay_position",
    "seal_entries",
    "sell_entries",
    "verify_seal",
]
