"""Service-level kill switch guard: an armed switch refuses new risk from every caller.

The global level is the switch's ``active`` flag and it applies to orders that carry no
strategy attribution just as much as to strategy orders. Only a buy can add risk: a sell
is bounded by the held quantity and can never open a short, so an exit still settles
while the switch is armed. The refusal happens before the quote network call.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select

from ezchain_api.models import LedgerEntryRow, PaperFillRow, PaperOrderRow
from ezchain_api.services import paper
from ezchain_api.services.system_flags import set_kill_switch

from .conftest import make_request, make_snapshot, place


def _ledger_entries(session) -> int:
    return int(session.scalar(select(func.count()).select_from(LedgerEntryRow)) or 0)


def test_armed_switch_refuses_a_manual_buy_before_any_quote(
    session, settings, dex, jupiter
) -> None:
    """A REST, MCP or web-ticket buy carries no strategy, and must still be refused."""
    paper.ensure_genesis(session, settings.paper_starting_cash_usd)
    journal_before = _ledger_entries(session)
    set_kill_switch(session, active=True, reason="review drill")

    order = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(amount_usd="100", key="manual-armed"),
    )

    assert order.status == "rejected"
    assert order.reason == "KILL_SWITCH_ARMED"
    assert order.strategy_id is None
    assert dex.calls == 0
    assert jupiter.calls == []
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 0
    assert _ledger_entries(session) == journal_before
    assert Decimal(paper.account_snapshot(session)["cash_usd"]) == Decimal("10000")


def test_armed_switch_refuses_a_strategy_attributed_buy_before_any_quote(
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
    assert order.reason == "KILL_SWITCH_ARMED"
    assert dex.calls == 0
    assert jupiter.calls == []
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 0


def test_armed_switch_still_allows_a_reduce_only_exit(session, settings, dex, jupiter) -> None:
    """Blocking an exit would trap funds, so an armed switch never refuses a sell."""
    dex.set(make_snapshot(price=Decimal("2"), liquidity=Decimal("500000")))
    opened = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(amount_usd="100", key="exit-open"),
    )
    assert opened.status == "filled"
    set_kill_switch(session, active=True, reason="review drill")

    exit_order = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(side="sell", amount_usd="100", key="exit-armed", reduce_only=True),
    )

    assert exit_order.status == "filled"
    assert exit_order.reason is None
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 2
    fill = session.scalar(select(PaperFillRow).where(PaperFillRow.order_id == exit_order.id))
    assert fill is not None
    assert fill.quantity == Decimal("50")


def test_released_switch_allows_an_order(session, settings, dex, jupiter) -> None:
    set_kill_switch(session, active=False, reason="drill complete")
    dex.set(make_snapshot(price=Decimal("2000")))

    order = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(amount_usd="100", key="released", strategy_id="strategy-1"),
    )

    assert order.status == "filled"
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 1
    assert session.scalar(select(func.count()).select_from(PaperOrderRow)) == 1
