"""Read-only ledger and execution reconciliation.

Everything here is derived from persisted rows: ledger entries, fills and orders. There is
no mutable balance scalar and nothing is repaired in place. The endpoint reports what the
database proves so an operator can decide whether to pause (CONTRACT I1, I9).

Each check has one source of truth:

* journal groups must balance and map to exactly one filled order and fill;
* the fill's quantity, notional and fee must equal its journal legs;
* position quantities must equal the sum of the fills that created them;
* cash and equity must agree with genesis plus the fill cash flows, at the current marks.

A position without a mark makes equity unknown. The check fails closed with the account
named rather than valuing the position at zero.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_core import utcnow
from ezchain_core.ledger import cash_account, fee_account, position_account

from ..models import LedgerEntryRow, MarketSnapshotRow, PaperFillRow, PaperOrderRow

TOLERANCE = Decimal("0.000001")
QUANTITY_TOLERANCE = Decimal("0.00000001")
POSITION_PREFIX = "position:"
GENESIS_ACCOUNT = "equity:genesis"
GENESIS_GROUP = "genesis"


def _is_funding_group(group_id: str, rows: list[Any]) -> bool:
    """A paper no-risk funding journal group: labelled and balanced on its own."""
    if group_id == "funding" or group_id.startswith("funding:") or group_id.startswith("funding-"):
        return True
    return bool(rows) and all(getattr(row, "kind", None) == "funding" for row in rows)


# The journal each order side must leave behind. A buy books two cash legs (trade and
# fee) and two position-side legs; a sell adds the realized PnL leg. A group that differs
# is linked to the order id but was not written by the one order path.
EXPECTED_KINDS = {
    "buy": Counter({"buy": 2, "fee": 2}),
    "sell": Counter({"sell": 2, "fee": 2, "pnl": 1}),
}


def _money(value: Decimal) -> str:
    """Canonical decimal string with no exponent and no float round-trip."""
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _sample(items: list[str], limit: int = 5) -> str:
    shown = ", ".join(items[:limit])
    return shown if len(items) <= limit else f"{shown}, … (+{len(items) - limit} more)"


def _check(check_id: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"id": check_id, "ok": ok, "detail": detail}


def _split_position(account: str) -> tuple[str, str]:
    chain, _, token = account[len(POSITION_PREFIX) :].partition(":")
    return chain, token


def _fills_cash(fills: list[Any], genesis: Decimal) -> Decimal:
    """Cash projected from genesis plus every persisted fill: sells add, buys subtract."""
    total = genesis
    for fill in fills:
        notional = Decimal(str(fill.notional_usd))
        fee = Decimal(str(fill.fee_usd))
        if fill.side == "sell":
            total += notional - fee
        else:
            total -= notional + fee
    return total


def _check_genesis(ledger: list[Any], fills: list[Any]) -> dict[str, Any]:
    """The starting cash is booked exactly once, as one cash leg and one equity leg."""
    markers = [row for row in ledger if row.account == GENESIS_ACCOUNT]
    if not ledger and not fills:
        return _check("genesis.accounted_once", True, "no journal yet")
    if not markers:
        return _check(
            "genesis.accounted_once",
            False,
            f"journal has {len(ledger)} entries but no {GENESIS_ACCOUNT} entry",
        )
    if len(markers) != 1:
        return _check(
            "genesis.accounted_once",
            False,
            f"{len(markers)} {GENESIS_ACCOUNT} entries; starting cash is counted more than once",
        )
    marker = markers[0]
    if marker.group_id != GENESIS_GROUP:
        return _check(
            "genesis.accounted_once",
            False,
            f"{GENESIS_ACCOUNT} is in group {marker.group_id!r}, not {GENESIS_GROUP!r}",
        )
    genesis_rows = [row for row in ledger if row.group_id == GENESIS_GROUP]
    cash_rows = [row for row in genesis_rows if row.account == cash_account()]
    unexpected = [
        row.account for row in genesis_rows if row.account not in {cash_account(), GENESIS_ACCOUNT}
    ]
    if len(genesis_rows) != 2 or len(cash_rows) != 1 or unexpected:
        return _check(
            "genesis.accounted_once",
            False,
            (
                f"genesis group has {len(genesis_rows)} entries "
                f"({len(cash_rows)} cash, {len(unexpected)} unexpected); "
                "expected one cash and one equity leg"
            ),
        )
    cash_row = cash_rows[0]
    if abs(marker.usd_value + cash_row.usd_value) > TOLERANCE:
        return _check(
            "genesis.accounted_once",
            False,
            (
                f"genesis legs do not offset: cash {_money(cash_row.usd_value)}, "
                f"equity {_money(marker.usd_value)}"
            ),
        )
    if abs(marker.amount - marker.usd_value) > TOLERANCE:
        return _check(
            "genesis.accounted_once",
            False,
            (
                f"genesis equity amount {_money(marker.amount)} is not its "
                f"USD value {_money(marker.usd_value)}"
            ),
        )
    return _check(
        "genesis.accounted_once",
        True,
        f"starting cash {_money(cash_row.usd_value)} booked once",
    )


def _check_groups_linked(
    by_group: dict[str, list[Any]],
    orders_by_id: dict[str, Any],
    fills_by_order: dict[str, list[Any]],
    fills: list[Any],
) -> dict[str, Any]:
    """Every non-genesis group maps to exactly one filled order and one matching fill."""
    orphan_groups: list[str] = []
    groups_without_fill: list[str] = []
    kind_mismatches: list[str] = []
    order_mismatches: list[str] = []
    for group_id, rows in by_group.items():
        if group_id == GENESIS_GROUP:
            continue
        if _is_funding_group(group_id, rows):
            continue
        order = orders_by_id.get(group_id)
        if order is None:
            orphan_groups.append(group_id)
            continue
        group_fills = fills_by_order.get(group_id, [])
        if len(group_fills) != 1:
            groups_without_fill.append(group_id)
            continue
        fill = group_fills[0]
        if order.status != "filled":
            order_mismatches.append(f"{group_id} is {order.status}, not filled")
        if (
            fill.side != order.side
            or fill.chain != order.chain
            or fill.token_address != order.token_address
        ):
            order_mismatches.append(f"{group_id} order and fill disagree")
        if abs(Decimal(str(order.amount_usd)) - Decimal(str(fill.notional_usd))) > TOLERANCE:
            order_mismatches.append(f"{group_id} order notional and fill notional disagree")
        expected = EXPECTED_KINDS.get(order.side)
        if expected is None or Counter(row.kind for row in rows) != expected:
            kind_mismatches.append(group_id)
            continue
        position_rows = [row for row in rows if row.account.startswith(POSITION_PREFIX)]
        if (
            len(position_rows) != 1
            or (fill.side == "buy" and position_rows[0].amount <= 0)
            or (fill.side == "sell" and position_rows[0].amount >= 0)
        ):
            kind_mismatches.append(group_id)

    fills_without_group = sorted(fill.order_id for fill in fills if fill.order_id not in by_group)
    problems: list[str] = []
    if orphan_groups:
        problems.append(
            f"{len(orphan_groups)} journal groups without an order: {_sample(orphan_groups)}"
        )
    if groups_without_fill:
        problems.append(
            f"{len(groups_without_fill)} order groups without exactly one fill: "
            f"{_sample(groups_without_fill)}"
        )
    if fills_without_group:
        problems.append(
            f"{len(fills_without_group)} fills without a journal group: "
            f"{_sample(fills_without_group)}"
        )
    if kind_mismatches:
        problems.append(
            f"{len(kind_mismatches)} groups with unexpected legs: {_sample(kind_mismatches)}"
        )
    if order_mismatches:
        problems.append(
            f"{len(order_mismatches)} order/fill mismatches: {_sample(order_mismatches)}"
        )
    linked = len(by_group) - (1 if GENESIS_GROUP in by_group else 0)
    if problems:
        return _check("ledger.groups_linked", False, "; ".join(problems))
    return _check(
        "ledger.groups_linked",
        True,
        f"{linked} order groups each link to one filled order, one fill and its journal legs",
    )


def _check_fill_values(fills: list[Any], by_group: dict[str, list[Any]]) -> dict[str, Any]:
    """Each fill's quantity, notional and fee equal the journal legs it claims."""
    problems: list[str] = []
    for fill in fills:
        rows = by_group.get(fill.order_id)
        if not rows:
            problems.append(f"{fill.id}: no journal group {fill.order_id}")
            continue
        notional = Decimal(str(fill.notional_usd))
        fee = Decimal(str(fill.fee_usd))
        quantity = Decimal(str(fill.quantity))
        cash_rows = [row for row in rows if row.account == cash_account()]
        trade_cash = sum(
            (Decimal(str(row.usd_value)) for row in cash_rows if row.kind == fill.side),
            Decimal(0),
        )
        cash_delta = sum((Decimal(str(row.usd_value)) for row in cash_rows), Decimal(0))
        fee_total = sum(
            (Decimal(str(row.usd_value)) for row in rows if row.account == fee_account()),
            Decimal(0),
        )
        position_rows = [
            row for row in rows if row.account == position_account(fill.chain, fill.token_address)
        ]
        ledger_quantity = sum((Decimal(str(row.amount)) for row in position_rows), Decimal(0))

        if fill.side == "sell":
            expected_trade_cash = notional
            expected_cash = notional - fee
            expected_quantity = -quantity
        else:
            expected_trade_cash = -notional
            expected_cash = -notional - fee
            expected_quantity = quantity
        differences: list[str] = []
        if abs(trade_cash - expected_trade_cash) > TOLERANCE:
            differences.append(f"trade cash {_money(trade_cash)} != {_money(expected_trade_cash)}")
        if abs(cash_delta - expected_cash) > TOLERANCE:
            differences.append(f"cash delta {_money(cash_delta)} != {_money(expected_cash)}")
        if abs(fee_total - fee) > TOLERANCE:
            differences.append(f"fee {_money(fee_total)} != {_money(fee)}")
        if abs(ledger_quantity - expected_quantity) > QUANTITY_TOLERANCE:
            differences.append(f"quantity {_money(ledger_quantity)} != {_money(expected_quantity)}")
        if differences:
            problems.append(f"{fill.id}: {'; '.join(differences)}")

    if problems:
        return _check(
            "fills.ledger_values_match",
            False,
            f"{len(problems)} fills disagree with their journal legs: {_sample(problems)}",
        )
    return _check(
        "fills.ledger_values_match",
        True,
        f"{len(fills)} fills match their journal quantity, notional and fee",
    )


def _check_position_quantities(
    position_quantities: dict[str, Decimal], from_fills: dict[str, Decimal]
) -> dict[str, Any]:
    """Ledger position quantity equals the sum of buys minus sells for that account."""
    mismatched: list[str] = []
    for account in sorted(set(position_quantities) | set(from_fills)):
        ledger_quantity = position_quantities.get(account, Decimal(0))
        fill_quantity = from_fills.get(account, Decimal(0))
        if abs(ledger_quantity - fill_quantity) > QUANTITY_TOLERANCE:
            mismatched.append(
                f"{account} ledger={_money(ledger_quantity)} fills={_money(fill_quantity)}"
            )
    if mismatched:
        return _check(
            "positions.fills_match",
            False,
            f"{len(mismatched)} position accounts disagree with fills: {_sample(mismatched)}",
        )
    return _check(
        "positions.fills_match",
        True,
        f"{len(from_fills)} position accounts equal the sum of their fills",
    )


def _check_equity(
    cash_total: Decimal,
    fills_cash: Decimal,
    genesis: Decimal,
    position_quantities: dict[str, Decimal],
    from_fills: dict[str, Decimal],
    fill_count: int,
    marks: dict[tuple[str, str], Decimal | None],
) -> dict[str, Any]:
    """Cash plus marked positions equals genesis plus the marked effects of every fill.

    A fill's effect is its cash delta plus the signed quantity it left behind, valued at
    the current mark. The two sides are computed from independent rows: cash and
    quantities from the journal, cash and quantities from the fills. An open position
    without a mark is unknown, never zero, so the check fails closed and names it.
    """
    unmarked: list[str] = []
    ledger_value = Decimal(0)
    for account, quantity in position_quantities.items():
        if abs(quantity) <= QUANTITY_TOLERANCE:
            continue
        mark = marks.get(_split_position(account))
        if mark is None or mark <= 0:
            unmarked.append(account)
        else:
            ledger_value += quantity * mark
    fill_value = Decimal(0)
    for account, quantity in from_fills.items():
        if abs(quantity) <= QUANTITY_TOLERANCE:
            continue
        mark = marks.get(_split_position(account))
        if mark is None or mark <= 0:
            if account not in unmarked:
                unmarked.append(account)
        else:
            fill_value += quantity * mark

    if unmarked:
        return _check(
            "equity.identity",
            False,
            f"equity unknown: no mark for {len(unmarked)} position(s): {_sample(unmarked)}",
        )
    equity_ledger = cash_total + ledger_value
    equity_fills = fills_cash + fill_value
    difference = equity_ledger - equity_fills
    if abs(difference) > TOLERANCE:
        return _check(
            "equity.identity",
            False,
            (
                f"ledger equity {_money(equity_ledger)} != genesis + fill effects "
                f"{_money(equity_fills)} (difference {_money(difference)})"
            ),
        )
    return _check(
        "equity.identity",
        True,
        (
            f"ledger equity {_money(equity_ledger)} equals genesis {_money(genesis)} "
            f"plus {fill_count} fill effects"
        ),
    )


def reconciliation(session: Session) -> dict[str, Any]:
    """Compute the reconciliation report. Never writes to the database."""
    # Load only the columns the report reads. The endpoint is polled by the dashboard,
    # alerts and readiness, and the random-walk test reconciles after every step; ORM
    # identity-map construction would dominate both.
    ledger = session.execute(
        select(
            LedgerEntryRow.group_id,
            LedgerEntryRow.account,
            LedgerEntryRow.amount,
            LedgerEntryRow.usd_value,
            LedgerEntryRow.kind,
        ).order_by(LedgerEntryRow.seq)
    ).all()
    fills = session.execute(
        select(
            PaperFillRow.id,
            PaperFillRow.order_id,
            PaperFillRow.chain,
            PaperFillRow.token_address,
            PaperFillRow.side,
            PaperFillRow.quantity,
            PaperFillRow.notional_usd,
            PaperFillRow.fee_usd,
            PaperFillRow.quote_kind,
        ).order_by(PaperFillRow.created_at, PaperFillRow.id)
    ).all()
    orders = session.execute(
        select(
            PaperOrderRow.id,
            PaperOrderRow.chain,
            PaperOrderRow.token_address,
            PaperOrderRow.side,
            PaperOrderRow.status,
            PaperOrderRow.amount_usd,
        )
    ).all()
    marks = {
        (row.chain, row.token_address): (
            None if row.price_usd is None else Decimal(str(row.price_usd))
        )
        for row in session.execute(
            select(
                MarketSnapshotRow.chain,
                MarketSnapshotRow.token_address,
                MarketSnapshotRow.price_usd,
            )
        )
    }

    group_totals: dict[str, Decimal] = defaultdict(Decimal)
    by_group: dict[str, list[Any]] = defaultdict(list)
    cash_total = Decimal(0)
    genesis = Decimal(0)
    funding_cash = Decimal(0)
    position_quantities: dict[str, Decimal] = defaultdict(Decimal)

    for row in ledger:
        value = Decimal(str(row.usd_value))
        amount = Decimal(str(row.amount))
        group_totals[row.group_id] += value
        by_group[row.group_id].append(row)
        if row.account == cash_account():
            cash_total += value
            if _is_funding_group(row.group_id, by_group[row.group_id]):
                funding_cash += value
        elif row.account == GENESIS_ACCOUNT:
            # The genesis equity leg is the negative of the starting cash.
            genesis -= value
        if row.account.startswith(POSITION_PREFIX):
            position_quantities[row.account] += amount

    # Independently project cash from genesis plus the persisted fill cash flow:
    # sells add proceeds, buys subtract notional, every fill pays its fee.
    fills_cash = _fills_cash(list(fills), genesis) + funding_cash

    fills_by_order: dict[str, list[Any]] = defaultdict(list)
    position_from_fills: dict[str, Decimal] = defaultdict(Decimal)
    for fill in fills:
        fills_by_order[fill.order_id].append(fill)
        account = position_account(fill.chain, fill.token_address)
        quantity = Decimal(str(fill.quantity))
        position_from_fills[account] += quantity if fill.side == "buy" else -quantity

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
        _check_genesis(list(ledger), list(fills)),
        _check(
            "ledger.groups_balanced",
            not unbalanced,
            (
                f"{len(group_totals)} groups checked; all sums within {TOLERANCE}"
                if not unbalanced
                else f"{len(unbalanced)} unbalanced groups: {_sample(unbalanced)}"
            ),
        ),
        _check_groups_linked(by_group, orders_by_id, fills_by_order, list(fills)),
        _check(
            "cash.fills_match",
            abs(cash_total - fills_cash) <= TOLERANCE,
            f"ledger cash {_money(cash_total)}; genesis + fills {_money(fills_cash)}",
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
        _check_position_quantities(position_quantities, position_from_fills),
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
        _check_fill_values(list(fills), by_group),
        _check_equity(
            cash_total,
            fills_cash,
            genesis,
            position_quantities,
            position_from_fills,
            len(fills),
            marks,
        ),
    ]

    totals = {
        "genesis_usd": _money(genesis),
        "cash_usd": _money(cash_total),
        "fills_cash_usd": _money(fills_cash),
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


__all__ = ["QUANTITY_TOLERANCE", "TOLERANCE", "reconciliation"]
