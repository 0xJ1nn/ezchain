"""Service-level kill switch guard for strategy-attributed paper orders."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select

from ezchain_api.models import PaperFillRow, PaperOrderRow
from ezchain_api.services import paper
from ezchain_api.services.system_flags import set_kill_switch

from .conftest import make_request, make_snapshot, place


def test_armed_switch_rejects_strategy_order_before_any_quote(
    session, settings, dex, jupiter
) -> None:
    set_kill_switch(session, active=True, reason="review drill")

    order = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(amount_usd="100", key="strategy-armed", strategy_id="strategy-1"),
    )

    assert order.status == "rejected"
    assert order.reason == "KILL_SWITCH_ACTIVE"
    assert dex.calls == 0
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 0
    assert Decimal(paper.account_snapshot(session)["cash_usd"]) == Decimal("10000")


def test_released_switch_allows_a_strategy_order(session, settings, dex, jupiter) -> None:
    set_kill_switch(session, active=False, reason="drill complete")
    dex.set(make_snapshot(price=Decimal("2000")))

    order = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(amount_usd="100", key="strategy-released", strategy_id="strategy-1"),
    )

    assert order.status == "filled"
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 1


def test_armed_switch_still_allows_manual_orders(session, settings, dex, jupiter) -> None:
    set_kill_switch(session, active=True, reason="review drill")
    dex.set(make_snapshot(price=Decimal("2000")))

    order = place(session, settings, dex, jupiter, make_request(amount_usd="100", key="manual"))

    assert order.status == "filled"
    assert order.strategy_id is None
    assert session.scalar(select(func.count()).select_from(PaperOrderRow)) == 1
