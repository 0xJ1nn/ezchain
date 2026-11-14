"""Read-only ledger and execution reconciliation.

Everything here is derived from persisted rows: ledger entries, fills and orders. There is
no mutable balance scalar and nothing is repaired in place. The endpoint reports what the
database proves so an operator can decide whether to pause (CONTRACT I1, I9).

Each check has one source of truth:

* journal groups must balance and map to exactly one filled order and fill; a no-risk
  funding group counts only when it is ``funding:{order.id}`` for an existing order and
  carries exactly the two legs paper writes for the shortfall;
* the fill's quantity, notional and fee must equal its journal legs;
* position quantities must equal the sum of the fills that created them;
* cash and equity must agree with genesis plus the fill cash flows, at the current marks;
* every strategy order must be named by a run row, so a crash between the order and the run
  log is reported instead of leaving an execution with no reasoning behind it.

A position without a mark makes equity unknown. The check fails closed with the account
named rather than valuing the position at zero.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import UTC
from decimal import Decimal, localcontext
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_core import utcnow
from ezchain_core.ledger import cash_account, fee_account, position_account
from ezchain_core.money import (
    MONEY_SCALE,
    MoneyError,
    exact_precision,
    exact_sum,
    from_minor_units,
    quantize_money,
    to_minor_units,
)

from ..models import (
    LedgerEntryRow,
    MarketSnapshotRow,
    PaperFillRow,
    PaperOrderRow,
    StrategyRow,
    StrategyRunRow,
)

# Quantities are token amounts, not money: they are stored at 18 decimals and a dust
# difference below one storage step is arithmetic noise, not a missing leg. Money is
# never compared this way — it is compared as exact integer minor units.
QUANTITY_TOLERANCE = Decimal("0.00000001")
POSITION_PREFIX = "position:"
GENESIS_ACCOUNT = "equity:genesis"
GENESIS_GROUP = "genesis"


FUNDING_PREFIX = "funding:"
FUNDING_ACCOUNT = "funding:USD"


def _minor(value: object) -> int | None:
    """Exact integer minor units, or ``None`` when the value is not exact money.

    A persisted row that is not a whole number of minor units is itself the finding: the
    caller reports it instead of comparing it. There is no epsilon here (LEDGER.md §2.6).
    """
    try:
        return to_minor_units(Decimal(str(value)))
    except MoneyError:
        return None


def _minor_sum(values: Sequence[object]) -> tuple[int, int]:
    """(exact minor-unit sum, number of values that could not be represented exactly)."""
    total = 0
    inexact = 0
    for value in values:
        minor = _minor(value)
        if minor is None:
            inexact += 1
        else:
            total += minor
    return total, inexact


def _computed_minor(value: Decimal) -> int:
    """A *computed* USD figure rounded once at the boundary, then read exactly.

    A position value (``quantity * mark``) is not a journaled amount, so it is rounded
    half-even once to the ledger minor unit — the same boundary rule the journal uses —
    and compared as an integer afterwards. No epsilon survives the comparison.
    """
    return to_minor_units(quantize_money(value, field="computed_usd"))


def _position_value_minor(quantity: Decimal, mark: Decimal) -> int:
    """``quantity * mark`` evaluated exactly, then rounded once at the ledger boundary."""
    with localcontext() as context:
        context.prec = exact_precision(quantity, mark, extra=MONEY_SCALE)
        value = quantity * mark
    return _computed_minor(value)


def _is_funding_group(group_id: str, rows: list[Any], orders_by_id: dict[str, Any]) -> bool:
    """A no-risk funding journal for an existing paper order, with the exact legs.

    The name alone proves nothing: a group called ``funding-2`` (or
    ``funding:missing-order``) is not the journal paper writes. Only the exact
    ``funding:{order.id}`` of a persisted order, carrying one positive cash leg and its
    equal negative funding leg, may be skipped by the group-link check.
    """
    if not group_id.startswith(FUNDING_PREFIX):
        return False
    order_id = group_id[len(FUNDING_PREFIX) :]
    if not order_id or order_id not in orders_by_id or len(rows) != 2:
        return False
    cash_rows = [row for row in rows if getattr(row, "account", None) == cash_account()]
    funding_rows = [row for row in rows if getattr(row, "account", None) == FUNDING_ACCOUNT]
    if len(cash_rows) != 1 or len(funding_rows) != 1:
        return False
    cash_row, funding_row = cash_rows[0], funding_rows[0]
    if any(getattr(row, "kind", None) != "funding" for row in (cash_row, funding_row)):
        return False
    funded = _minor(cash_row.usd_value)
    contra = _minor(funding_row.usd_value)
    if funded is None or contra is None:
        return False
    return funded > 0 and funded + contra == 0


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
    with localcontext() as context:
        context.prec = exact_precision(value, extra=MONEY_SCALE)
        return format(value.normalize(), "f")


def _usd(minor: int) -> str:
    """Report an integer minor-unit total as exact USD."""
    return _money(from_minor_units(minor))


def _sample(items: list[str], limit: int = 5) -> str:
    shown = ", ".join(items[:limit])
    return shown if len(items) <= limit else f"{shown}, … (+{len(items) - limit} more)"


def _check(check_id: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"id": check_id, "ok": ok, "detail": detail}


def _split_position(account: str) -> tuple[str, str]:
    chain, _, token = account[len(POSITION_PREFIX) :].partition(":")
    return chain, token


def _fills_cash_minor(fills: Sequence[Any], genesis_minor: int) -> tuple[int, int]:
    """(projected cash in minor units, fills whose figures are not exact money).

    Cash is projected from genesis plus every persisted fill as *integer* minor units:
    sells add proceeds, buys subtract notional, every fill pays its fee. A fill figure
    that does not land on the quantum is counted, never rounded into the projection.
    """
    total = genesis_minor
    inexact = 0
    for fill in fills:
        notional = _minor(fill.notional_usd)
        fee = _minor(fill.fee_usd)
        if notional is None or fee is None:
            inexact += 1
            continue
        if fill.side == "sell":
            total += notional - fee
        else:
            total -= notional + fee
    return total, inexact


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
    marker_minor = _minor(marker.usd_value)
    cash_minor = _minor(cash_row.usd_value)
    if marker_minor is None or cash_minor is None:
        return _check(
            "genesis.accounted_once",
            False,
            (
                f"genesis legs are not exact money: cash {_money(cash_row.usd_value)}, "
                f"equity {_money(marker.usd_value)}"
            ),
        )
    if marker_minor + cash_minor != 0:
        return _check(
            "genesis.accounted_once",
            False,
            (
                f"genesis legs do not offset: cash {_money(cash_row.usd_value)}, "
                f"equity {_money(marker.usd_value)}, "
                f"{marker_minor + cash_minor} minor units out"
            ),
        )
    amount_minor = _minor(marker.amount)
    if amount_minor != marker_minor:
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
    funding_groups = [
        group_id
        for group_id, rows in by_group.items()
        if _is_funding_group(group_id, rows, orders_by_id)
    ]
    for group_id, rows in by_group.items():
        if group_id == GENESIS_GROUP:
            continue
        if _is_funding_group(group_id, rows, orders_by_id):
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
        # Two values that are not exact money are not "equal" either: `None == None` would
        # read a sub-minor pair as a match, so an unrepresentable figure fails the check.
        order_notional = _minor(order.amount_usd)
        fill_notional = _minor(fill.notional_usd)
        if order_notional is None or fill_notional is None:
            order_mismatches.append(
                f"{group_id} order notional or fill notional is not a whole number of minor units"
            )
        elif order_notional != fill_notional:
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
    linked = len(by_group) - (1 if GENESIS_GROUP in by_group else 0) - len(funding_groups)
    if problems:
        return _check("ledger.groups_linked", False, "; ".join(problems))
    detail = f"{linked} order groups each link to one filled order, one fill and its journal legs"
    if funding_groups:
        detail += f"; {len(funding_groups)} funding groups match a real order"
    return _check("ledger.groups_linked", True, detail)


def _check_fill_values(fills: list[Any], by_group: dict[str, list[Any]]) -> dict[str, Any]:
    """Each fill's quantity, notional and fee equal the journal legs it claims.

    Money is compared as exact integer minor units; a fill whose persisted figure is not
    on the quantum is itself a failure and is named rather than compared.
    """
    problems: list[str] = []
    for fill in fills:
        rows = by_group.get(fill.order_id)
        if not rows:
            problems.append(f"{fill.id}: no journal group {fill.order_id}")
            continue
        notional = _minor(fill.notional_usd)
        fee = _minor(fill.fee_usd)
        quantity = Decimal(str(fill.quantity))
        if notional is None or fee is None:
            problems.append(f"{fill.id}: notional or fee is not a whole number of minor units")
            continue
        cash_rows = [row for row in rows if row.account == cash_account()]
        trade_cash, trade_inexact = _minor_sum(
            [row.usd_value for row in cash_rows if row.kind == fill.side]
        )
        cash_delta, cash_inexact = _minor_sum([row.usd_value for row in cash_rows])
        fee_total, fee_inexact = _minor_sum(
            [row.usd_value for row in rows if row.account == fee_account()]
        )
        position_rows = [
            row for row in rows if row.account == position_account(fill.chain, fill.token_address)
        ]
        ledger_quantity = exact_sum([row.amount for row in position_rows])

        if fill.side == "sell":
            expected_trade_cash = notional
            expected_cash = notional - fee
            expected_quantity = quantity.copy_negate()
        else:
            expected_trade_cash = -notional
            expected_cash = -notional - fee
            expected_quantity = quantity
        differences: list[str] = []
        if trade_inexact or cash_inexact or fee_inexact:
            differences.append("journal cash or fee leg is not a whole number of minor units")
        if not trade_inexact and trade_cash != expected_trade_cash:
            differences.append(f"trade cash {_usd(trade_cash)} != {_usd(expected_trade_cash)}")
        if not cash_inexact and cash_delta != expected_cash:
            differences.append(f"cash delta {_usd(cash_delta)} != {_usd(expected_cash)}")
        if not fee_inexact and fee_total != fee:
            differences.append(f"fee {_usd(fee_total)} != {_usd(fee)}")
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
    cash_minor: int,
    fills_cash_minor: int,
    genesis_minor: int,
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

    Both sides are accumulated as integer minor units. A marked position value is a
    *computed* figure, so it is rounded once at the ledger boundary (half-even) before it
    is added; the comparison itself is exact integer equality, with no epsilon.
    """
    unmarked: list[str] = []
    ledger_value_minor = 0
    for account, quantity in position_quantities.items():
        if abs(quantity) <= QUANTITY_TOLERANCE:
            continue
        mark = marks.get(_split_position(account))
        if mark is None or mark <= 0:
            unmarked.append(account)
        else:
            ledger_value_minor += _position_value_minor(quantity, mark)
    fill_value_minor = 0
    for account, quantity in from_fills.items():
        if abs(quantity) <= QUANTITY_TOLERANCE:
            continue
        mark = marks.get(_split_position(account))
        if mark is None or mark <= 0:
            if account not in unmarked:
                unmarked.append(account)
        else:
            fill_value_minor += _position_value_minor(quantity, mark)

    if unmarked:
        return _check(
            "equity.identity",
            False,
            f"equity unknown: no mark for {len(unmarked)} position(s): {_sample(unmarked)}",
        )
    equity_ledger_minor = cash_minor + ledger_value_minor
    equity_fills_minor = fills_cash_minor + fill_value_minor
    difference = equity_ledger_minor - equity_fills_minor
    if difference != 0:
        return _check(
            "equity.identity",
            False,
            (
                f"ledger equity {_usd(equity_ledger_minor)} != genesis + fill effects "
                f"{_usd(equity_fills_minor)} (difference {_usd(difference)})"
            ),
        )
    return _check(
        "equity.identity",
        True,
        (
            f"ledger equity {_usd(equity_ledger_minor)} equals genesis {_usd(genesis_minor)} "
            f"plus {fill_count} fill effects"
        ),
    )


def _check_strategy_runs(
    orders: list[Any], runs: list[Any], strategy_ids: set[str]
) -> dict[str, Any]:
    """Every live strategy's execution names at least one run row that reasoned about it.

    The engine commits the decision claim before it places the order and records the
    order id on that row afterwards, so a crash in between leaves an order whose run row
    never learned its id. That gap is reported, not repaired: the money is real and the
    reasoning behind it is missing (CONTRACT I9). An order whose strategy was deleted is
    skipped, because deleting a strategy intentionally removes its decision log (ADR-0022).

    Several rows naming one order is the log working, not drift: a refused execution
    releases its window, so each retry of the same order reasons about it again and leaves
    its own refusal behind.
    """
    named = {run.order_id for run in runs if run.order_id is not None}
    live = [
        order
        for order in orders
        if order.strategy_id is not None and order.strategy_id in strategy_ids
    ]
    unreconciled = [order.id for order in live if order.id not in named]
    if unreconciled:
        return _check(
            "strategy.runs_linked",
            False,
            f"{len(unreconciled)} strategy order(s) no run row names: {_sample(unreconciled)}",
        )
    return _check(
        "strategy.runs_linked",
        True,
        f"{len(live)} strategy order(s) each named by a run row",
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
            PaperOrderRow.strategy_id,
        )
    ).all()
    runs = session.execute(
        select(StrategyRunRow.order_id).where(StrategyRunRow.order_id.is_not(None))
    ).all()
    strategy_ids = set(session.scalars(select(StrategyRow.id)).all())
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

    # Money is accumulated as integer minor units. A persisted value that is not a whole
    # number of minor units is not compared: it is recorded as inexact and reported, so a
    # sub-quantum row can never hide inside a sum (LEDGER.md §2.6).
    group_totals: dict[str, int] = defaultdict(int)
    group_inexact: set[str] = set()
    by_group: dict[str, list[Any]] = defaultdict(list)
    cash_minor = 0
    genesis_minor = 0
    position_amounts: dict[str, list[Any]] = defaultdict(list)
    orders_by_id = {order.id: order for order in orders}

    for row in ledger:
        by_group[row.group_id].append(row)
        if row.account.startswith(POSITION_PREFIX):
            # A position quantity is a quantity, not money: it is summed for every row,
            # whatever its USD value is, so an inexact money leg cannot shrink the
            # position the ledger actually holds.
            position_amounts[row.account].append(row.amount)
        value = _minor(row.usd_value)
        if value is None:
            group_inexact.add(row.group_id)
            continue
        group_totals[row.group_id] += value
        if row.account == cash_account():
            cash_minor += value
        elif row.account == GENESIS_ACCOUNT:
            # The genesis equity leg is the negative of the starting cash.
            genesis_minor -= value

    # Quantities are summed exactly, with a precision derived from the operands, so the
    # result does not depend on the ambient context or on the row order.
    position_quantities: dict[str, Decimal] = {
        account: exact_sum(amounts) for account, amounts in position_amounts.items()
    }

    # Only a funding group tied to a real order adds to projected cash. A group that
    # merely looks like funding is left to the orphan and cash checks to report.
    funding_minor = 0
    for group_id, rows in by_group.items():
        if not _is_funding_group(group_id, rows, orders_by_id):
            continue
        for row in rows:
            if row.account == cash_account():
                # `_is_funding_group` already refused a leg that is not exact money, so a
                # funding group that reaches here contributes an integer, never a None.
                funding_minor += _minor(row.usd_value) or 0

    # Independently project cash from genesis plus the persisted fill cash flow:
    # sells add proceeds, buys subtract notional, every fill pays its fee.
    fills_cash_minor, fills_inexact = _fills_cash_minor(fills, genesis_minor)
    fills_cash_minor += funding_minor

    fills_by_order: dict[str, list[Any]] = defaultdict(list)
    fill_amounts: dict[str, list[Any]] = defaultdict(list)
    for fill in fills:
        fills_by_order[fill.order_id].append(fill)
        account = position_account(fill.chain, fill.token_address)
        signed = Decimal(str(fill.quantity))
        fill_amounts[account].append(signed if fill.side == "buy" else -signed)
    position_from_fills = {account: exact_sum(amounts) for account, amounts in fill_amounts.items()}

    unbalanced = sorted(
        group_id
        for group_id in by_group
        if group_totals.get(group_id, 0) != 0 or group_id in group_inexact
    )
    negative_positions = sorted(
        account for account, quantity in position_quantities.items() if quantity < 0
    )
    open_positions = sum(1 for quantity in position_quantities.values() if quantity > 0)

    fills_per_order = Counter(fill.order_id for fill in fills)
    orphan_fills = sorted(fill.id for fill in fills if fill.order_id not in orders_by_id)
    filled_orders = [order for order in orders if order.status == "filled"]
    orders_without_single_fill = sorted(
        order.id for order in filled_orders if fills_per_order.get(order.id, 0) != 1
    )
    quote_kinds = Counter(fill.quote_kind for fill in fills)

    cash_matches = cash_minor == fills_cash_minor and not fills_inexact
    checks = [
        _check_genesis(list(ledger), list(fills)),
        _check(
            "ledger.groups_balanced",
            not unbalanced,
            (
                f"{len(group_totals)} groups checked; every sum is exactly zero minor units"
                if not unbalanced
                else f"{len(unbalanced)} unbalanced groups: {_sample(unbalanced)}"
            ),
        ),
        _check_groups_linked(by_group, orders_by_id, fills_by_order, list(fills)),
        _check_strategy_runs(list(orders), list(runs), strategy_ids),
        _check(
            "cash.fills_match",
            cash_matches,
            f"ledger cash {_usd(cash_minor)}; genesis + fills {_usd(fills_cash_minor)}"
            + (f" ({fills_inexact} fill figures are not exact money)" if fills_inexact else ""),
        ),
        _check(
            "cash.non_negative",
            cash_minor >= 0,
            f"cash account sum {_usd(cash_minor)}",
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
            cash_minor,
            fills_cash_minor,
            genesis_minor,
            position_quantities,
            position_from_fills,
            len(fills),
            marks,
        ),
    ]

    totals = {
        "genesis_usd": _usd(genesis_minor),
        "cash_usd": _usd(cash_minor),
        "fills_cash_usd": _usd(fills_cash_minor),
        "open_positions": open_positions,
        "fills": len(fills),
        "filled_orders": len(filled_orders),
        "orphan_fills": len(orphan_fills),
        "filled_orders_without_single_fill": len(orders_without_single_fill),
        "negative_cash": 1 if cash_minor < 0 else 0,
        "negative_positions": len(negative_positions),
        "quote_kinds": dict(sorted(quote_kinds.items())),
    }
    return {
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
        "as_of": utcnow().astimezone(UTC).isoformat(),
        "totals": totals,
    }


__all__ = ["QUANTITY_TOLERANCE", "reconciliation"]
