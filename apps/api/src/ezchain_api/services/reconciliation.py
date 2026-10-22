"""Read-only ledger and execution reconciliation.

Everything here is derived from persisted rows: ledger entries, fills and orders. There is
no mutable balance scalar and nothing is repaired in place. The endpoint reports what the
database proves so an operator can decide whether to pause (CONTRACT I1, I9).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_core import utcnow
from ezchain_core.ledger import cash_account

from ..models import LedgerEntryRow, PaperFillRow, PaperOrderRow

TOLERANCE = Decimal("0.000001")
POSITION_PREFIX = "position:"


def _money(value: Decimal) -> str:
    """Decimal string with no exponent and no float round-trip."""
    return format(value, "f")


def _sample(items: list[str], limit: int = 5) -> str:
    shown = ", ".join(items[:limit])
    return shown if len(items) <= limit else f"{shown}, … (+{len(items) - limit} more)"


def _check(check_id: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"id": check_id, "ok": ok, "detail": detail}


def reconciliation(session: Session) -> dict[str, Any]:
    """Compute the reconciliation report. Never writes to the database."""
    ledger = list(
        session.scalars(
            select(LedgerEntryRow).order_by(LedgerEntryRow.created_at, LedgerEntryRow.id)
        )
    )
    fills = list(session.scalars(select(PaperFillRow)))
    orders = list(session.scalars(select(PaperOrderRow)))

    group_totals: dict[str, Decimal] = defaultdict(Decimal)
    cash_total = Decimal(0)
    replay_cash = Decimal(0)
    position_quantities: dict[str, Decimal] = defaultdict(Decimal)

    for row in ledger:
        value = Decimal(str(row.usd_value))
        amount = Decimal(str(row.amount))
        group_totals[row.group_id] += value
        if row.account == cash_account():
            cash_total += value
        if row.account.startswith(POSITION_PREFIX):
            position_quantities[row.account] += amount

    # Replay the cash account in ledger order, separately from the group aggregation.
    for row in ledger:
        if row.account == cash_account():
            replay_cash += Decimal(str(row.usd_value))

    unbalanced = [group_id for group_id, total in group_totals.items() if abs(total) > TOLERANCE]
    negative_positions = sorted(
        account for account, quantity in position_quantities.items() if quantity < 0
    )
    open_positions = sum(1 for quantity in position_quantities.values() if quantity > 0)

    orders_by_id = {order.id: order for order in orders}
    fills_per_order = Counter(fill.order_id for fill in fills)
    orphan_fills = sorted(fill.id for fill in fills if fill.order_id not in orders_by_id)
    filled_orders = [order for order in orders if order.status == "filled"]
    orders_without_single_fill = sorted(
        order.id for order in filled_orders if fills_per_order.get(order.id, 0) != 1
    )
    quote_kinds = Counter(fill.quote_kind for fill in fills)

    checks = [
        _check(
            "ledger.groups_balanced",
            not unbalanced,
            (
                f"{len(group_totals)} groups checked; all sums within {TOLERANCE}"
                if not unbalanced
                else f"{len(unbalanced)} unbalanced groups: {_sample(unbalanced)}"
            ),
        ),
        _check(
            "cash.replay_matches",
            cash_total == replay_cash,
            f"cash account sum {_money(cash_total)}; replayed cash {_money(replay_cash)}",
        ),
        _check(
            "cash.non_negative",
            cash_total >= 0,
            f"cash account sum {_money(cash_total)}",
        ),
        _check(
            "positions.non_negative",
            not negative_positions,
            (
                "all position quantities are >= 0"
                if not negative_positions
                else f"{len(negative_positions)} negative positions: {_sample(negative_positions)}"
            ),
        ),
        _check(
            "fills.orphaned",
            not orphan_fills,
            (
                f"{len(fills)} fills reference an existing order"
                if not orphan_fills
                else f"{len(orphan_fills)} orphan fills: {_sample(orphan_fills)}"
            ),
        ),
        _check(
            "orders.filled_single_fill",
            not orders_without_single_fill,
            (
                f"{len(filled_orders)} filled orders have exactly one fill"
                if not orders_without_single_fill
                else f"{len(orders_without_single_fill)} filled orders without exactly "
                f"one fill: {_sample(orders_without_single_fill)}"
            ),
        ),
    ]

    totals = {
        "cash_usd": _money(cash_total),
        "replay_cash_usd": _money(replay_cash),
        "open_positions": open_positions,
        "fills": len(fills),
        "filled_orders": len(filled_orders),
        "orphan_fills": len(orphan_fills),
        "filled_orders_without_single_fill": len(orders_without_single_fill),
        "negative_cash": 1 if cash_total < 0 else 0,
        "negative_positions": len(negative_positions),
        "quote_kinds": dict(sorted(quote_kinds.items())),
    }
    return {
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
        "as_of": utcnow().astimezone(UTC).isoformat(),
        "totals": totals,
    }


__all__ = ["TOLERANCE", "reconciliation"]
