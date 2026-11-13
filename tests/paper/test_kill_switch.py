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
from ezchain_api.services.system_flags import KillSwitch as StoredKillSwitch
from ezchain_api.services.system_flags import set_kill_switch

from .conftest import FakeDexScreener, make_request, make_snapshot, place


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


def test_an_unreadable_switch_refuses_a_buy_with_a_typed_code(
    session, settings, dex, jupiter, monkeypatch
) -> None:
    """An unreadable flag is a typed refusal, not a raw exception out of place_order."""
    paper.ensure_genesis(session, settings.paper_starting_cash_usd)
    journal_before = _ledger_entries(session)

    def unavailable(db, config):  # type: ignore[no-untyped-def]
        raise RuntimeError("system_flags unreadable")

    monkeypatch.setattr(paper, "read_kill_switch", unavailable)

    order = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(amount_usd="100", key="unreadable-pre"),
    )

    assert order.status == "rejected"
    assert order.reason == "KILL_SWITCH_UNAVAILABLE"
    assert dex.calls == 0
    assert jupiter.calls == []
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 0
    assert _ledger_entries(session) == journal_before


def test_an_unreadable_switch_in_the_fill_window_refuses_with_a_typed_code(
    session, settings, dex, jupiter, monkeypatch
) -> None:
    """The mid-flight read fails unknown: fail closed with the typed code, book nothing."""
    paper.ensure_genesis(session, settings.paper_starting_cash_usd)
    journal_before = _ledger_entries(session)
    released = StoredKillSwitch(active=False, reason=None, updated_at=None)
    calls = {"n": 0}

    def released_then_unreadable(db, config):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("system_flags unreadable")
        return released

    monkeypatch.setattr(paper, "read_kill_switch", released_then_unreadable)

    order = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(amount_usd="100", key="unreadable-fill"),
    )

    assert order.status == "rejected"
    assert order.reason == "KILL_SWITCH_UNAVAILABLE"
    assert calls["n"] >= 2
    assert dex.calls >= 1  # the fill-window read happens after the market read
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 0
    assert _ledger_entries(session) == journal_before


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


def test_a_buy_already_in_flight_is_refused_when_the_switch_is_armed(
    session, settings, jupiter
) -> None:
    """The gate before the market read is not enough: arm after it and the fill refuses."""
    paper.ensure_genesis(session, settings.paper_starting_cash_usd)
    journal_before = _ledger_entries(session)

    class ArmingDex(FakeDexScreener):
        async def get_token(  # type: ignore[no-untyped-def]
            self, chain, address, *, max_age_seconds=None
        ):
            snapshot = await super().get_token(chain, address, max_age_seconds=max_age_seconds)
            set_kill_switch(session, active=True, reason="armed mid-flight")
            return snapshot

    dex = ArmingDex(make_snapshot(price=Decimal("2000")))

    order = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(amount_usd="100", key="mid-flight-buy"),
    )

    assert order.status == "rejected"
    assert order.reason == "KILL_SWITCH_ARMED"
    assert dex.calls >= 1  # the market read happened before the refusal, not after it
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 0
    assert _ledger_entries(session) == journal_before


def test_an_exit_already_in_flight_still_settles_when_the_switch_is_armed(
    session, settings, jupiter
) -> None:
    """The mid-flight gate must not trap funds: an exit still books its reduce-only fill."""
    dex = FakeDexScreener(make_snapshot(price=Decimal("2000"), liquidity=Decimal("500000")))
    opened = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(amount_usd="100", key="mid-flight-open"),
    )
    assert opened.status == "filled"

    class ArmingDex(FakeDexScreener):
        async def get_token(  # type: ignore[no-untyped-def]
            self, chain, address, *, max_age_seconds=None
        ):
            snapshot = await super().get_token(chain, address, max_age_seconds=max_age_seconds)
            set_kill_switch(session, active=True, reason="armed mid-flight")
            return snapshot

    armed_dex = ArmingDex(make_snapshot(price=Decimal("2000"), liquidity=Decimal("500000")))
    exit_order = place(
        session,
        settings,
        armed_dex,
        jupiter,
        make_request(side="sell", amount_usd="100", key="mid-flight-exit", reduce_only=True),
    )

    assert exit_order.status == "filled"
    assert exit_order.reason is None
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 2
