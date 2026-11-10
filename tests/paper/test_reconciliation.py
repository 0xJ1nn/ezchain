"""Reconciliation read model: clean accounts pass, persisted corruption is reported."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ezchain_api.models import (
    LedgerEntryRow,
    MarketSnapshotRow,
    PaperFillRow,
    PaperOrderRow,
    new_id,
)
from ezchain_api.schemas import ReconciliationOut
from ezchain_api.services import paper
from ezchain_api.services.reconciliation import TOLERANCE, reconciliation

from .conftest import CHAIN, TOKEN, make_request, make_snapshot, place

NEW_CHECK_IDS = {
    "genesis.accounted_once",
    "ledger.groups_linked",
    "positions.fills_match",
    "fills.ledger_values_match",
    "equity.identity",
}
EXISTING_CHECK_IDS = {
    "ledger.groups_balanced",
    "cash.fills_match",
    "cash.non_negative",
    "positions.non_negative",
    "fills.orphaned",
    "orders.filled_single_fill",
}


def _checks(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(check["id"]): check for check in report["checks"]}


def _buy(session: Session, settings, dex, jupiter, *, amount_usd: str = "100") -> PaperOrderRow:
    dex.set(make_snapshot(price=Decimal("2000"), liquidity=Decimal("500000")))
    order = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(amount_usd=amount_usd, key=f"recon-{new_id()}"),
    )
    assert order.status == "filled"
    return order


def test_clean_genesis_account_is_ok(session: Session, settings) -> None:
    paper.ensure_genesis(session, settings.paper_starting_cash_usd)

    report = reconciliation(session)

    assert report["ok"] is True
    assert {check["id"] for check in report["checks"]} == EXISTING_CHECK_IDS | NEW_CHECK_IDS
    checks = _checks(report)
    assert checks["genesis.accounted_once"]["ok"] is True
    assert "10000" in checks["genesis.accounted_once"]["detail"]
    totals = report["totals"]
    assert totals["genesis_usd"] == "10000"
    assert totals["cash_usd"] == "10000"
    assert totals["fills_cash_usd"] == "10000"
    assert totals["open_positions"] == 0
    assert totals["fills"] == 0
    assert totals["quote_kinds"] == {}


def test_buy_and_sell_reconcile_and_counts_quote_kinds(
    session: Session, settings, dex, jupiter
) -> None:
    dex.set(make_snapshot(price=Decimal("2000"), liquidity=Decimal("500000")))
    place(session, settings, dex, jupiter, make_request(amount_usd="200", key="recon-buy"))
    dex.set(make_snapshot(price=Decimal("2500"), liquidity=Decimal("500000")))
    place(
        session,
        settings,
        dex,
        jupiter,
        make_request(side="sell", amount_usd="100", key="recon-sell"),
    )

    report = reconciliation(session)

    assert report["ok"] is True
    checks = _checks(report)
    assert checks["ledger.groups_balanced"]["ok"] is True
    assert checks["ledger.groups_linked"]["ok"] is True
    assert checks["positions.fills_match"]["ok"] is True
    assert checks["fills.ledger_values_match"]["ok"] is True
    assert checks["equity.identity"]["ok"] is True
    totals = report["totals"]
    assert totals["fills"] == 2
    assert totals["filled_orders"] == 2
    assert totals["open_positions"] == 1
    assert totals["orphan_fills"] == 0
    assert totals["quote_kinds"] == {"paper_mark": 2}
    account = paper.account_snapshot(session)
    assert Decimal(totals["cash_usd"]) == Decimal(account["cash_usd"])
    assert Decimal(totals["fills_cash_usd"]) == Decimal(account["cash_usd"])
    assert totals["genesis_usd"] == "10000"


def test_fill_cash_flow_mismatch_is_reported(session: Session, settings, dex, jupiter) -> None:
    _buy(session, settings, dex, jupiter)

    # Corrupt only the fill notional: the journal and order/fill counts stay sound, so
    # the independent genesis+fills projection and the fill/journal leg comparison both
    # catch the divergence.
    fill = session.scalar(select(PaperFillRow))
    assert fill is not None
    fill.notional_usd = Decimal("101")
    session.commit()

    report = reconciliation(session)

    checks = _checks(report)
    assert report["ok"] is False
    assert checks["cash.fills_match"]["ok"] is False
    assert checks["fills.ledger_values_match"]["ok"] is False
    assert checks["ledger.groups_balanced"]["ok"] is True
    assert checks["fills.orphaned"]["ok"] is True
    assert checks["orders.filled_single_fill"]["ok"] is True
    totals = report["totals"]
    assert totals["cash_usd"] == "9899.7"
    assert totals["fills_cash_usd"] == "9898.7"


def test_unbalanced_group_orphan_fill_and_missing_fill_are_reported(
    session: Session, settings, dex, jupiter
) -> None:
    _buy(session, settings, dex, jupiter)

    # A filled order with no fill at all.
    session.add(
        PaperOrderRow(
            id="order-without-fill",
            idempotency_key="missing-fill",
            chain="ethereum",
            token_address="0x0000000000000000000000000000000000000abc",
            side="buy",
            amount_usd=Decimal("10"),
            status="filled",
        )
    )
    # A fill whose order does not exist.
    session.add(
        PaperFillRow(
            id="orphan-fill",
            order_id="does-not-exist",
            chain="ethereum",
            token_address="0x0000000000000000000000000000000000000abc",
            side="buy",
            quantity=Decimal("1"),
            price_usd=Decimal("10"),
            notional_usd=Decimal("10"),
            fee_usd=Decimal("0"),
            quote_kind="paper_mark",
        )
    )
    # A journal group that does not sum to zero.
    session.add(
        LedgerEntryRow(
            id="drifting-entry",
            group_id="drift-group",
            account="cash:USD",
            asset="USD",
            amount=Decimal("1"),
            usd_value=Decimal("1"),
            kind="drift",
        )
    )
    session.commit()

    report = reconciliation(session)

    assert report["ok"] is False
    checks = _checks(report)
    assert checks["ledger.groups_balanced"]["ok"] is False
    assert "drift-group" in str(checks["ledger.groups_balanced"]["detail"])
    assert checks["fills.orphaned"]["ok"] is False
    assert checks["orders.filled_single_fill"]["ok"] is False
    assert checks["ledger.groups_linked"]["ok"] is False
    assert "drift-group" in checks["ledger.groups_linked"]["detail"]
    assert "does-not-exist" in checks["ledger.groups_linked"]["detail"]
    assert checks["fills.ledger_values_match"]["ok"] is False
    totals = report["totals"]
    assert totals["orphan_fills"] == 1
    assert totals["filled_orders_without_single_fill"] == 1


def test_balanced_group_without_an_order_or_fill_is_reported(session: Session, settings) -> None:
    paper.ensure_genesis(session, settings.paper_starting_cash_usd)
    # A balanced group that moves a position against PnL and never
    # touches cash. Group balance and the cash projection both pass; only the link and
    # fill-quantity checks can see it.
    session.add_all(
        [
            LedgerEntryRow(
                id="rogue-position",
                group_id="rogue-adjustment",
                account="position:blast:0xrogue",
                asset="0xrogue",
                amount=Decimal("2"),
                usd_value=Decimal("20"),
                kind="buy",
            ),
            LedgerEntryRow(
                id="rogue-pnl",
                group_id="rogue-adjustment",
                account="pnl:realized:USD",
                asset="USD",
                amount=Decimal("-20"),
                usd_value=Decimal("-20"),
                kind="pnl",
            ),
        ]
    )
    session.commit()

    report = reconciliation(session)

    assert report["ok"] is False
    checks = _checks(report)
    assert checks["ledger.groups_balanced"]["ok"] is True
    assert checks["cash.fills_match"]["ok"] is True
    assert checks["ledger.groups_linked"]["ok"] is False
    assert "rogue-adjustment" in checks["ledger.groups_linked"]["detail"]
    assert checks["positions.fills_match"]["ok"] is False
    assert "position:blast:0xrogue" in checks["positions.fills_match"]["detail"]


def _corrupt_missing_group(session: Session) -> dict[str, str]:
    order = session.scalar(select(PaperOrderRow))
    assert order is not None
    session.execute(delete(LedgerEntryRow).where(LedgerEntryRow.group_id == order.id))
    return {
        "ledger.groups_linked": order.id,
        "fills.ledger_values_match": order.id,
    }


def _corrupt_duplicated_group(session: Session) -> dict[str, str]:
    order = session.scalar(select(PaperOrderRow))
    fill = session.scalar(select(PaperFillRow))
    assert order is not None and fill is not None
    rows = list(session.scalars(select(LedgerEntryRow).where(LedgerEntryRow.group_id == order.id)))
    assert rows
    for row in rows:
        session.add(
            LedgerEntryRow(
                id=new_id(),
                group_id=row.group_id,
                account=row.account,
                asset=row.asset,
                amount=row.amount,
                usd_value=row.usd_value,
                kind=row.kind,
                created_at=row.created_at,
            )
        )
    return {
        "ledger.groups_linked": order.id,
        "fills.ledger_values_match": fill.id,
    }


def _corrupt_wrong_sign(session: Session) -> dict[str, str]:
    order = session.scalar(select(PaperOrderRow))
    fill = session.scalar(select(PaperFillRow))
    assert order is not None and fill is not None
    row = session.scalar(
        select(LedgerEntryRow).where(
            LedgerEntryRow.group_id == order.id,
            LedgerEntryRow.account == "cash:USD",
            LedgerEntryRow.kind == "buy",
        )
    )
    assert row is not None
    row.usd_value = -row.usd_value
    return {
        "ledger.groups_balanced": order.id,
        "fills.ledger_values_match": fill.id,
    }


def _corrupt_order_notional(session: Session) -> dict[str, str]:
    order = session.scalar(select(PaperOrderRow))
    assert order is not None
    order.amount_usd = order.amount_usd + Decimal("1")
    return {"ledger.groups_linked": order.id}


def _corrupt_orphan_fill(session: Session) -> dict[str, str]:
    orphan_order_id = new_id()
    fill_id = new_id()
    session.add(
        PaperFillRow(
            id=fill_id,
            order_id=orphan_order_id,
            chain=CHAIN,
            token_address=TOKEN,
            side="buy",
            quantity=Decimal("1"),
            price_usd=Decimal("10"),
            notional_usd=Decimal("10"),
            fee_usd=Decimal("0"),
            quote_kind="paper_mark",
        )
    )
    return {
        "fills.orphaned": fill_id,
        "ledger.groups_linked": orphan_order_id,
    }


def _corrupt_mismatched_position(session: Session) -> dict[str, str]:
    order = session.scalar(select(PaperOrderRow))
    fill = session.scalar(select(PaperFillRow))
    assert order is not None and fill is not None
    row = session.scalar(
        select(LedgerEntryRow).where(
            LedgerEntryRow.group_id == order.id,
            LedgerEntryRow.account.like("position:%"),
        )
    )
    assert row is not None
    row.amount = row.amount / 2
    return {
        "positions.fills_match": row.account,
        "fills.ledger_values_match": fill.id,
    }


CORRUPTIONS: list[Callable[[Session], dict[str, str]]] = [
    _corrupt_missing_group,
    _corrupt_duplicated_group,
    _corrupt_wrong_sign,
    _corrupt_order_notional,
    _corrupt_orphan_fill,
    _corrupt_mismatched_position,
]


def _corruption_id(fn: Callable[[Session], dict[str, str]]) -> str:
    return fn.__name__.removeprefix("_corrupt_")


@pytest.mark.parametrize("corrupt", CORRUPTIONS, ids=_corruption_id)
def test_one_corrupted_fixture_row_is_reported(
    session: Session, settings, dex, jupiter, corrupt: Callable[[Session], dict[str, str]]
) -> None:
    _buy(session, settings, dex, jupiter)

    expected = corrupt(session)
    session.commit()

    report = reconciliation(session)

    checks = _checks(report)
    assert report["ok"] is False
    for check_id, marker in expected.items():
        assert checks[check_id]["ok"] is False, (check_id, checks[check_id])
        assert marker in checks[check_id]["detail"]

    # The endpoint serialises this report through this model; a failing check id must
    # survive the response boundary, not be dropped as an unknown field.
    response = ReconciliationOut.model_validate(report)
    assert response.ok is False
    failed_ids = {check.id for check in response.checks if not check.ok}
    assert set(expected) <= failed_ids


def test_second_genesis_leg_is_reported_even_when_the_books_balance(
    session: Session, settings
) -> None:
    paper.ensure_genesis(session, settings.paper_starting_cash_usd)
    # A second balanced funding journal doubles cash and the genesis equity leg together,
    # so every cash-vs-fills projection still agrees while starting cash is counted twice.
    session.add_all(
        [
            LedgerEntryRow(
                id="funding-cash",
                group_id="funding-2",
                account="cash:USD",
                asset="USD",
                amount=Decimal("5000"),
                usd_value=Decimal("5000"),
                kind="genesis",
            ),
            LedgerEntryRow(
                id="funding-equity",
                group_id="funding-2",
                account="equity:genesis",
                asset="USD",
                amount=Decimal("-5000"),
                usd_value=Decimal("-5000"),
                kind="genesis",
            ),
        ]
    )
    session.commit()

    report = reconciliation(session)

    checks = _checks(report)
    assert checks["ledger.groups_balanced"]["ok"] is True
    assert checks["cash.fills_match"]["ok"] is True
    assert checks["genesis.accounted_once"]["ok"] is False
    assert "counted more than once" in checks["genesis.accounted_once"]["detail"]


def test_missing_genesis_is_reported(session: Session) -> None:
    session.add(
        LedgerEntryRow(
            id="unfunded-cash",
            group_id="unfunded",
            account="cash:USD",
            asset="USD",
            amount=Decimal("10"),
            usd_value=Decimal("10"),
            kind="funding",
        )
    )
    session.commit()

    report = reconciliation(session)

    check = _checks(report)["genesis.accounted_once"]
    assert check["ok"] is False
    assert "no equity:genesis entry" in check["detail"]


def test_equity_identity_fails_closed_on_an_unknown_mark(
    session: Session, settings, dex, jupiter
) -> None:
    _buy(session, settings, dex, jupiter)
    session.execute(delete(MarketSnapshotRow))
    session.commit()

    report = reconciliation(session)

    checks = _checks(report)
    assert checks["equity.identity"]["ok"] is False
    detail = checks["equity.identity"]["detail"]
    assert "equity unknown" in detail
    assert "position:blast:0x0000000000000000000000000000000000000abc" in detail
    assert paper.account_snapshot(session)["equity_usd"] is None


def test_equity_identity_uses_the_current_mark(session: Session, settings, dex, jupiter) -> None:
    _buy(session, settings, dex, jupiter)
    snapshot = session.scalar(select(MarketSnapshotRow))
    assert snapshot is not None
    snapshot.price_usd = Decimal("3000")
    session.commit()

    report = reconciliation(session)

    check = _checks(report)["equity.identity"]
    assert check["ok"] is True
    # Cash 9899.7 plus 0.05 tokens at 3000 = 10049.7.
    assert "10049.7" in check["detail"]


def test_negative_cash_and_negative_position_are_reported(session: Session, settings) -> None:
    session.add_all(
        [
            LedgerEntryRow(
                id="negative-cash",
                group_id="negative-group",
                account="cash:USD",
                asset="USD",
                amount=Decimal("-5"),
                usd_value=Decimal("-5"),
                kind="drift",
            ),
            LedgerEntryRow(
                id="negative-position",
                group_id="negative-group",
                account="position:ethereum:0xtoken",
                asset="0xtoken",
                amount=Decimal("-1"),
                usd_value=Decimal("5"),
                kind="drift",
            ),
        ]
    )
    session.commit()

    report = reconciliation(session)

    checks = _checks(report)
    assert checks["cash.non_negative"]["ok"] is False
    assert checks["positions.non_negative"]["ok"] is False
    assert report["totals"]["negative_cash"] == 1
    assert report["totals"]["negative_positions"] == 1


def test_group_tolerance_is_one_micro_dollar(session: Session) -> None:
    session.add(
        LedgerEntryRow(
            id="tiny-drift",
            group_id="tiny-group",
            account="cash:USD",
            asset="USD",
            amount=TOLERANCE,
            usd_value=TOLERANCE,
            kind="rounding",
        )
    )
    session.commit()

    report = reconciliation(session)
    assert _checks(report)["ledger.groups_balanced"]["ok"] is True
    assert session.scalar(select(LedgerEntryRow.id)) == "tiny-drift"
