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
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from typing import Protocol
from uuid import uuid4

from .money import (
    bps_fee,
    exact_neg,
    exact_precision,
    exact_sum,
    quantize_money,
    require_money,
    require_quantity,
    to_minor_units,
)

# v2 encodes each link as canonical JSON, so field values cannot run into one another
# and two different entries never produce the same payload. v1 joined raw fields with
# "|", which was ambiguous: account "position:base:0xa|b" with asset "T" and account
# "position:base:0xa" with asset "b|T" serialized identically.
LEDGER_DIGEST_SCHEMA = "ezchain.ledger/v2"
LEDGER_GENESIS_DIGEST = "0" * 64


class LedgerError(ValueError):
    """The tape violates a ledger invariant."""


class LedgerTamperedError(LedgerError):
    """A tape no longer matches the seal recorded for it."""


def _canonical_decimal(value: Decimal) -> str:
    """A stable, exponent-free form: ``1.5`` and ``1.50`` hash the same."""
    if value == 0:
        return "0"
    with localcontext() as context:
        context.prec = exact_precision(value)
        return format(value.normalize(), "f")


@dataclass(frozen=True)
class LedgerEntry:
    """One journal leg, with the attribution the seal must cover.

    ``group_id``, ``created_at``, ``wallet_id``, the agent bucket and the actor are
    optional because a computed journal (a factory result) is attributeless until the
    caller stamps it. When present they are part of the digest payload: a tape whose
    wallet, date, group, bucket or actor changed is a different tape, not the same one
    with the same head.
    """

    account: str
    asset: str
    amount: Decimal
    usd_value: Decimal
    kind: str
    group_id: str | None = None
    created_at: datetime | None = None
    wallet_id: str | None = None
    bucket_id: str | None = None
    actor_kind: str | None = None
    actor_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", require_quantity(self.amount, field="amount"))
        object.__setattr__(self, "usd_value", require_money(self.usd_value, field="usd_value"))
        if self.created_at is not None and self.created_at.tzinfo is None:
            raise LedgerError("created_at must be timezone-aware")

    def to_row(self, group_id: str, created_at: datetime | None = None) -> dict[str, object]:
        if self.group_id is not None and self.group_id != group_id:
            raise LedgerError(
                f"entry belongs to group {self.group_id!r}, not {group_id!r}; "
                "a sealed tape must be the tape that is persisted"
            )
        if created_at is not None and self.created_at is not None and created_at != self.created_at:
            raise LedgerError("entry timestamp and the row timestamp disagree")
        row: dict[str, object] = {
            "id": str(uuid4()),
            "group_id": group_id,
            "account": self.account,
            "asset": self.asset,
            "amount": self.amount,
            "usd_value": self.usd_value,
            "kind": self.kind,
            "created_at": created_at or self.created_at or datetime.now(UTC),
        }
        if self.wallet_id is not None:
            row["wallet_id"] = self.wallet_id
        if self.bucket_id is not None:
            row["bucket_id"] = self.bucket_id
        if self.actor_kind is not None:
            row["actor_kind"] = self.actor_kind
        if self.actor_id is not None:
            row["actor_id"] = self.actor_id
        return row


class SealedRow(Protocol):
    """The persisted columns a sealed entry is rebuilt from.

    ``ledger_entries`` has exactly these columns; the protocol keeps core free of an
    import from the API's SQLAlchemy models while the seal still covers the attribution.
    """

    group_id: str
    account: str
    asset: str
    amount: Decimal
    usd_value: Decimal
    kind: str
    wallet_id: str | None
    bucket_id: str | None
    actor_kind: str | None
    actor_id: str | None
    created_at: datetime


def entry_from_row(row: SealedRow) -> LedgerEntry:
    """Rebuild the sealed :class:`LedgerEntry` for one persisted journal row.

    The seal is taken over this projection, so it covers the attribution the row carries
    (group, timestamp, wallet, agent bucket, actor) and not only its arithmetic. SQLite
    returns a naive datetime for a timestamp stored in UTC; it is read back as UTC.
    """
    created_at = row.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return LedgerEntry(
        account=row.account,
        asset=row.asset,
        amount=row.amount,
        usd_value=row.usd_value,
        kind=row.kind,
        group_id=row.group_id,
        created_at=created_at,
        wallet_id=row.wallet_id,
        bucket_id=row.bucket_id,
        actor_kind=row.actor_kind,
        actor_id=row.actor_id,
    )


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


# The contra account a no-risk paper top-up credits against cash. It is not equity and not
# PnL: funded capital must stay distinguishable from trading results (§LEDGER.md 3).
FUNDING_ACCOUNT = "funding:USD"
FUNDING_PREFIX = "funding:"


def position_account(chain: str, token_address: str) -> str:
    return f"position:{chain}:{token_address}"


def genesis_entries(starting_cash_usd: Decimal) -> tuple[LedgerEntry, ...]:
    starting_cash_usd = require_money(starting_cash_usd, field="starting_cash_usd")
    if starting_cash_usd <= 0:
        raise ValueError("starting cash must be positive")
    return (
        LedgerEntry(cash_account(), "USD", starting_cash_usd, starting_cash_usd, "genesis"),
        LedgerEntry(
            "equity:genesis",
            "USD",
            exact_neg(starting_cash_usd),
            exact_neg(starting_cash_usd),
            "genesis",
        ),
    )


def funding_entries(amount_usd: Decimal) -> tuple[LedgerEntry, ...]:
    """Journal for a no-risk paper top-up: cash +amount, ``funding:USD`` -amount.

    This is the only credit path in the ledger (genesis is the other inbound leg, and it
    happens once per deployment). There is no withdrawal or transfer journal: paper cash
    leaves the book only by being spent on a fill or a fee. The contra leg is deliberately
    *not* an equity or PnL account — a top-up is capital moved in, not income earned — so
    read models can tell funded cash from trading results.
    """
    amount = require_money(amount_usd, field="funded_usd")
    if amount <= 0:
        raise ValueError("funded amount must be positive")
    entries = (
        LedgerEntry(cash_account(), "USD", amount, amount, "funding"),
        LedgerEntry(FUNDING_ACCOUNT, "USD", exact_neg(amount), exact_neg(amount), "funding"),
    )
    assert_balanced(entries)
    return entries


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
        LedgerEntry(cash_account(), "USD", exact_neg(notional), exact_neg(notional), "buy"),
        LedgerEntry(fee_account(), "USD", fee, fee, "fee"),
        LedgerEntry(cash_account(), "USD", exact_neg(fee), exact_neg(fee), "fee"),
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
    realized = exact_sum([proceeds, exact_neg(cost_basis)])
    entries = (
        LedgerEntry(cash_account(), "USD", proceeds, proceeds, "sell"),
        LedgerEntry(
            position_account(chain, token_address),
            token_address,
            exact_neg(quantity),
            exact_neg(cost_basis),
            "sell",
        ),
        LedgerEntry(cash_account(), "USD", exact_neg(fee), exact_neg(fee), "fee"),
        LedgerEntry(fee_account(), "USD", fee, fee, "fee"),
        LedgerEntry(realized_account(), "USD", exact_neg(realized), exact_neg(realized), "pnl"),
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


def _canonical_timestamp(value: datetime | None) -> str | None:
    """Fixed-width UTC form: identical on every platform and tz library."""
    if value is None:
        return None
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _chain_payload(index: int, entry: LedgerEntry) -> dict[str, object]:
    """The exact, unambiguous object one link commits to.

    Every field is a JSON scalar, so no value can absorb a neighbour's text — the
    property the old ``"|"``-joined payload lacked. ``None`` is explicit rather than
    omitted: an entry that loses its wallet is a different entry, not a shorter one.
    """
    return {
        "schema": LEDGER_DIGEST_SCHEMA,
        "index": index,
        "account": entry.account,
        "asset": entry.asset,
        "amount": _canonical_decimal(entry.amount),
        "usd_value": _canonical_decimal(entry.usd_value),
        "kind": entry.kind,
        "group_id": entry.group_id,
        "created_at": _canonical_timestamp(entry.created_at),
        "wallet_id": entry.wallet_id,
        "bucket_id": entry.bucket_id,
        "actor_kind": entry.actor_kind,
        "actor_id": entry.actor_id,
    }


def _chain(head: str, index: int, entry: LedgerEntry) -> str:
    """One link: SHA-256 over the head, the index and the canonical JSON payload.

    The head is a fixed 64-hex string and the index is decimal, so the two framing
    fields are themselves unambiguous; the payload is JSON, so no field value can merge
    with another.
    """
    payload = json.dumps(
        {"previous": head, "entry": _chain_payload(index, entry)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
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
    account-sorted result. Amounts are summed *exactly*, at a precision derived from the
    operands, so neither the ambient context nor the tape length can leave a residue; USD
    is summed as integers, so there is nothing to drift.
    """
    amounts: dict[str, list[Decimal]] = {}
    usd: dict[str, int] = {}
    for entry in entries:
        amounts.setdefault(entry.account, []).append(entry.amount)
        usd[entry.account] = usd.get(entry.account, 0) + to_minor_units(entry.usd_value)
    return tuple(
        AccountBalance(account=account, amount=exact_sum(amounts[account]), usd_minor=usd[account])
        for account in sorted(amounts)
    )


def replay_position(entries: Iterable[LedgerEntry]) -> tuple[Decimal, Decimal]:
    """Return (quantity, cost_basis_usd) for one position account.

    The caller must pass entries in ledger order (oldest first). Replay is intentionally
    order-dependent because a sell removes cost basis at the then-current average.
    """
    quantities: list[Decimal] = []
    costs: list[Decimal] = []
    for entry in entries:
        quantities.append(entry.amount)
        costs.append(entry.usd_value)
    quantity = exact_sum(quantities)
    cost = exact_sum(costs)
    if quantity < 0:
        raise ValueError("position quantity cannot be negative")
    if quantity == 0:
        cost = Decimal(0)
    return quantity, cost


def realized_pnl(entries: Iterable[LedgerEntry]) -> Decimal:
    return -exact_sum([entry.usd_value for entry in entries if entry.account == realized_account()])


__all__ = [
    "LEDGER_DIGEST_SCHEMA",
    "LEDGER_GENESIS_DIGEST",
    "AccountBalance",
    "LedgerEntry",
    "LedgerError",
    "LedgerSeal",
    "LedgerTamperedError",
    "SealedRow",
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
