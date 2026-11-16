"""Paper no-risk mode: a capped, audited shortfall top-up (GH-106)."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from ezchain_api.models import LedgerEntryRow, PaperOrderRow
from ezchain_api.services import account_read, paper
from ezchain_core.ledger import cash_account

from .conftest import (
    make_request,
    make_settings,
    make_snapshot,
    place,
)

STARTING_CASH = Decimal("10")


def _funding_rows(session: Session, order_id: str) -> list[LedgerEntryRow]:
    return list(
        session.scalars(
            select(LedgerEntryRow).where(LedgerEntryRow.group_id == f"funding:{order_id}")
        )
    )


def _no_risk(**overrides: object):
    return make_settings(
        paper_no_risk=True,
        paper_starting_cash_usd=STARTING_CASH,
        **overrides,
    )


def test_disabled_mode_rejects_without_funding(session, dex, jupiter) -> None:
    """With the flag off an overdraw is still a plain refusal: no money is minted."""
    dex.set(make_snapshot(price=Decimal("2000"), liquidity=Decimal("500000")))
    settings = make_settings(paper_starting_cash_usd=STARTING_CASH)

    order = place(session, settings, dex, jupiter, make_request(amount_usd="100", key="off-fund"))

    assert order.status == "rejected"
    assert order.reason == "INSUFFICIENT_CASH"
    assert order.funding_json is None
    assert _funding_rows(session, order.id) == []


def test_enabled_mode_funds_exact_shortfall_once_with_full_audit(session, dex, jupiter) -> None:
    """One balanced funding group, the exact shortfall, and the whole decision on the order."""
    dex.set(make_snapshot(price=Decimal("2000"), liquidity=Decimal("500000")))
    settings = _no_risk()

    order = place(session, settings, dex, jupiter, make_request(amount_usd="100", key="on-fund"))

    assert order.status == "filled"
    assert order.reason == "SHORTFALL_FUNDED"
    rows = _funding_rows(session, order.id)
    assert len(rows) == 2
    cash_leg = next(row for row in rows if row.account == cash_account())
    assert Decimal(str(cash_leg.usd_value)) == Decimal("90.3")  # 100 notional + 0.30 fee - 10 cash
    audit = order.funding_json
    assert audit is not None
    assert audit["trigger"] == "INSUFFICIENT_CASH"
    assert Decimal(audit["shortfall_usd"]) == Decimal("90.3")
    assert Decimal(audit["cash_before_usd"]) == Decimal("10")
    assert Decimal(audit["cash_after_usd"]) == Decimal("100.3")
    assert audit["group_id"] == f"funding:{order.id}"


def test_no_ghost_trigger_when_cash_covers_the_ticket(session, dex, jupiter) -> None:
    """No shortfall means no funding group and no audit record, even with the flag on."""
    dex.set(make_snapshot(price=Decimal("2000"), liquidity=Decimal("500000")))
    settings = make_settings(paper_no_risk=True, paper_starting_cash_usd=Decimal("10000"))

    order = place(session, settings, dex, jupiter, make_request(amount_usd="100", key="no-ghost"))

    assert order.status == "filled"
    assert order.reason is None
    assert order.funding_json is None
    assert _funding_rows(session, order.id) == []


def test_cap_reached_rejects_with_a_stable_reason_and_an_alert(
    session, dex, jupiter, caplog
) -> None:
    """A shortfall past the cap is a refusal, never a smaller silent top-up."""
    dex.set(make_snapshot(price=Decimal("2000"), liquidity=Decimal("500000")))
    settings = _no_risk(paper_no_risk_topup_cap_usd=Decimal("50"))

    with caplog.at_level(logging.WARNING, logger="ezchain.paper.topup"):
        order = place(session, settings, dex, jupiter, make_request(amount_usd="100", key="capped"))

    assert order.status == "rejected"
    assert order.reason == "TOPUP_CAP_REACHED"
    assert order.reasons is not None
    assert order.reasons[0]["code"] == "TOPUP_CAP_REACHED"
    assert _funding_rows(session, order.id) == []
    audit = order.funding_json
    assert audit is not None
    assert Decimal(audit["shortfall_usd"]) == Decimal("90.3")
    # Nothing was funded, so the cash figure did not move.
    assert Decimal(audit["cash_before_usd"]) == Decimal(audit["cash_after_usd"])
    assert "paper_topup_cap_reached" in caplog.text

    feed = account_read.alerts(session, settings)
    codes = {item["code"] for item in feed["alerts"]}
    assert "PAPER_TOPUP_CAP_REACHED" in codes


def test_a_sell_never_funds(session, dex, jupiter) -> None:
    """No-risk funding is an entry-only gate; an exit is bounded by the position."""
    dex.set(make_snapshot(price=Decimal("2000"), liquidity=Decimal("500000")))
    settings = _no_risk()
    place(session, settings, dex, jupiter, make_request(amount_usd="5", key="sell-fund-buy"))

    sell = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(side="sell", quantity="0.001", key="sell-fund-exit"),
    )

    assert sell.status == "filled"
    assert sell.funding_json is None
    assert _funding_rows(session, sell.id) == []


def test_replay_of_a_funded_key_creates_no_second_topup(session, dex, jupiter) -> None:
    """An idempotency replay returns the stored order; it never funds the ticket twice."""
    dex.set(make_snapshot(price=Decimal("2000"), liquidity=Decimal("500000")))
    settings = _no_risk()
    request = make_request(amount_usd="100", key="replay-fund")

    first = place(session, settings, dex, jupiter, request)
    second = place(session, settings, dex, jupiter, request)

    assert first.id == second.id
    assert paper.last_created() is False
    assert len(_funding_rows(session, first.id)) == 2
    assert session.scalar(select(func.count()).select_from(PaperOrderRow)) == 1


def test_concurrent_retries_create_one_funded_order(tmp_path, dex, jupiter) -> None:
    """Two attempts at the same key under the account lock produce one funding group."""
    from threading import Barrier

    from ezchain_api.models import Base

    dex.set(make_snapshot(price=Decimal("2000"), liquidity=Decimal("500000")))
    settings = _no_risk()
    request = make_request(amount_usd="100", key="concurrent-fund")
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'topup-race.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    # Seed the account first. The wallet race is a separate concern; this test is about one
    # funding group per idempotency key.
    with maker() as seed:
        paper.ensure_genesis(seed, STARTING_CASH)
    barrier = Barrier(2)

    def attempt() -> str:
        with maker() as db:
            barrier.wait(timeout=10)
            return place(db, settings, dex, jupiter, request).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: attempt(), range(2)))

    assert ids[0] == ids[1]
    with maker() as db:
        assert db.scalar(select(func.count()).select_from(PaperOrderRow)) == 1
        assert len(_funding_rows(db, ids[0])) == 2
    engine.dispose()
