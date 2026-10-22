"""Append-only paper ledger.

Every entry carries both the asset amount and its USD value at the time of the fill.
For each order group, SUM(usd_value) must be zero. Balances and positions are derived by
replay; there is no mutable balance scalar.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from .money import bps_fee


@dataclass(frozen=True)
class LedgerEntry:
    account: str
    asset: str
    amount: Decimal
    usd_value: Decimal
    kind: str

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


def cash_account() -> str:
    return "cash:USD"


def fee_account() -> str:
    return "fees:USD"


def realized_account() -> str:
    return "pnl:realized:USD"


def position_account(chain: str, token_address: str) -> str:
    return f"position:{chain}:{token_address}"


def genesis_entries(starting_cash_usd: Decimal) -> tuple[LedgerEntry, ...]:
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
) -> tuple[LedgerEntry, ...]:
    if quantity <= 0 or price_usd <= 0:
        raise ValueError("quantity and price must be positive")
    notional = quantity * price_usd
    fee = bps_fee(notional, fee_bps)
    entries = (
        LedgerEntry(
            position_account(chain, token_address),
            token_address,
            quantity,
            notional,
            "buy",
        ),
        LedgerEntry(cash_account(), "USD", -(notional + fee), -(notional + fee), "buy"),
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
) -> tuple[LedgerEntry, ...]:
    if quantity <= 0 or price_usd <= 0:
        raise ValueError("quantity and price must be positive")
    if avg_cost_usd < 0:
        raise ValueError("average cost cannot be negative")
    proceeds = quantity * price_usd
    fee = bps_fee(proceeds, fee_bps)
    cash_in = proceeds - fee
    cost_basis = quantity * avg_cost_usd
    realized = proceeds - cost_basis - fee
    entries = (
        LedgerEntry(cash_account(), "USD", cash_in, cash_in, "sell"),
        LedgerEntry(
            position_account(chain, token_address),
            token_address,
            -quantity,
            -cost_basis,
            "sell",
        ),
        LedgerEntry(fee_account(), "USD", fee, fee, "fee"),
        LedgerEntry(realized_account(), "USD", -realized, -realized, "pnl"),
    )
    assert_balanced(entries)
    return entries


def assert_balanced(
    entries: Iterable[LedgerEntry], tolerance: Decimal = Decimal("0.000001")
) -> None:
    total = sum((e.usd_value for e in entries), Decimal(0))
    if abs(total) > tolerance:
        raise ValueError(f"ledger group does not balance: {total}")


def replay_position(entries: Iterable[LedgerEntry]) -> tuple[Decimal, Decimal]:
    """Return (quantity, cost_basis_usd) for one position account.

    The caller must pass entries in ledger order (oldest first). Replay is intentionally
    order-dependent because a sell removes cost basis at the then-current average.
    """
    quantity = Decimal(0)
    cost = Decimal(0)
    for entry in entries:
        quantity += entry.amount
        cost += entry.usd_value
    if quantity < 0:
        raise ValueError("position quantity cannot be negative")
    if quantity == 0:
        cost = Decimal(0)
    return quantity, cost


def realized_pnl(entries: Iterable[LedgerEntry]) -> Decimal:
    return -sum((e.usd_value for e in entries if e.account == realized_account()), Decimal(0))
