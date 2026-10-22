"""Balances and positions are rebuilt from the ledger after a restart."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from ezchain_api.models import Base, LedgerEntryRow
from ezchain_api.services import paper

from .conftest import (
    FakeDexScreener,
    FakeJupiter,
    make_request,
    make_settings,
    make_snapshot,
    place,
)


def test_replay_after_restart_preserves_balances_and_positions(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'paper.db'}"
    settings = make_settings()
    dex = FakeDexScreener(make_snapshot(price=Decimal("2000"), liquidity=Decimal("500000")))
    jupiter = FakeJupiter()

    first_engine = create_engine(url)
    Base.metadata.create_all(first_engine)
    with Session(first_engine, expire_on_commit=False) as session:
        buy = place(session, settings, dex, jupiter, make_request(amount_usd="200", key="r-buy"))
        assert buy.status == "filled"
        dex.set(make_snapshot(price=Decimal("2500"), liquidity=Decimal("500000")))
        sell = place(
            session,
            settings,
            dex,
            jupiter,
            make_request(side="sell", amount_usd="100", key="r-sell"),
        )
        assert sell.status == "filled"
        before_account = paper.account_snapshot(session)
        before_positions = paper.positions(session)
    first_engine.dispose()

    second_engine = create_engine(url)
    with Session(second_engine, expire_on_commit=False) as session:
        # A fresh process must not create a second genesis journal.
        assert session.scalar(select(func.count()).select_from(LedgerEntryRow)) == 11

        after_account = paper.account_snapshot(session)
        after_positions = paper.positions(session)

        assert after_account == before_account
        assert after_positions == before_positions
        assert Decimal(after_account["cash_usd"]) == Decimal("9899.1")
        assert after_positions[0]["symbol"] == "TEST"
        assert Decimal(after_positions[0]["mark_usd"] or "0") == Decimal("2500")

        # Trading continues from the replayed state, not from memory.
        dex.set(make_snapshot(price=Decimal("3000"), liquidity=Decimal("500000")))
        more = place(session, settings, dex, jupiter, make_request(amount_usd="100", key="r-after"))
        assert more.status == "filled"
        resumed = paper.account_snapshot(session)
        assert Decimal(resumed["cash_usd"]) == Decimal("9899.1") - Decimal("100.3")
        assert Decimal(resumed["unrealized_usd"]) > Decimal(after_account["unrealized_usd"])
    second_engine.dispose()
