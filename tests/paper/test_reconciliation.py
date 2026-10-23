"""Reconciliation read model: clean accounts pass, persisted corruption is reported."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_api.models import LedgerEntryRow, PaperFillRow, PaperOrderRow
from ezchain_api.services import paper
from ezchain_api.services.reconciliation import TOLERANCE, reconciliation

from .conftest import make_request, make_snapshot, place


def _checks(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(check["id"]): check for check in report["checks"]}


def test_clean_genesis_account_is_ok(session: Session, settings) -> None:
    paper.ensure_genesis(session, settings.paper_starting_cash_usd)

    report = reconciliation(session)

    assert report["ok"] is True
    assert {check["id"] for check in report["checks"]} == {
        "ledger.groups_balanced",
        "cash.fills_match",
        "cash.non_negative",
        "positions.non_negative",
        "fills.orphaned",
        "orders.filled_single_fill",
    }
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
    place(
        session,
        settings,
        dex,
        jupiter,
        make_request(amount_usd="200", key="recon-buy", risk_profile="high"),
    )
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
    assert _checks(report)["ledger.groups_balanced"]["ok"] is True
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
    dex.set(make_snapshot(price=Decimal("2000")))
    place(session, settings, dex, jupiter, make_request(amount_usd="100", key="recon-fill"))

    # Corrupt only the fill notional: the journal and order/fill counts stay sound, so
    # only the independent genesis+fills projection can catch the divergence.
    fill = session.scalar(select(PaperFillRow))
    assert fill is not None
    fill.notional_usd = Decimal("101")
    session.commit()

    report = reconciliation(session)

    checks = _checks(report)
    assert report["ok"] is False
    assert checks["cash.fills_match"]["ok"] is False
    assert checks["ledger.groups_balanced"]["ok"] is True
    assert checks["fills.orphaned"]["ok"] is True
    assert checks["orders.filled_single_fill"]["ok"] is True
    totals = report["totals"]
    assert totals["cash_usd"] == "9899.7"
    assert totals["fills_cash_usd"] == "9898.7"


def test_unbalanced_group_orphan_fill_and_missing_fill_are_reported(
    session: Session, settings, dex, jupiter
) -> None:
    dex.set(make_snapshot(price=Decimal("2000")))
    place(session, settings, dex, jupiter, make_request(amount_usd="100", key="recon-clean"))

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
    totals = report["totals"]
    assert totals["orphan_fills"] == 1
    assert totals["filled_orders_without_single_fill"] == 1


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
