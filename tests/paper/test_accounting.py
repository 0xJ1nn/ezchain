"""Buy/sell reconciliation and ledger invariants."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ezchain_api.models import LedgerEntryRow, PaperFillRow, PaperOrderRow
from ezchain_api.services import paper
from ezchain_core import utcnow

from .conftest import (
    CHAIN,
    SYMBOL,
    TOKEN,
    FakeDexScreener,
    FakeJupiter,
    make_request,
    make_snapshot,
    place,
)


def _group_sums(session: Session) -> dict[str, Decimal]:
    sums: dict[str, Decimal] = defaultdict(Decimal)
    for row in session.scalars(select(LedgerEntryRow)).all():
        sums[row.group_id] += row.usd_value
    return sums


def _money(value: str) -> Decimal:
    return Decimal(value)


def test_genesis_cannot_be_inserted_twice(session) -> None:
    paper.ensure_genesis(session, Decimal("10000"))
    session.add(
        LedgerEntryRow(
            group_id="genesis",
            account="cash:USD",
            asset="USD",
            amount=Decimal("10000"),
            usd_value=Decimal("10000"),
            kind="genesis",
            created_at=utcnow(),
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    assert session.scalar(select(func.count()).select_from(LedgerEntryRow)) == 2


def test_buy_then_sell_reconciles(session, settings, dex, jupiter) -> None:
    dex.set(make_snapshot(price=Decimal("2000"), liquidity=Decimal("500000")))
    buy = place(session, settings, dex, jupiter, make_request(amount_usd="200", key="buy-1"))
    assert buy.status == "filled"
    assert buy.filled_at is not None

    dex.set(make_snapshot(price=Decimal("2500"), liquidity=Decimal("500000")))
    sell = place(
        session, settings, dex, jupiter, make_request(side="sell", amount_usd="100", key="sell-1")
    )
    assert sell.status == "filled"

    buy_fill = session.scalar(select(PaperFillRow).where(PaperFillRow.order_id == buy.id))
    sell_fill = session.scalar(select(PaperFillRow).where(PaperFillRow.order_id == sell.id))
    assert buy_fill is not None and sell_fill is not None
    assert buy_fill.quantity == Decimal("0.1")
    assert buy_fill.fee_usd == Decimal("0.6")
    assert sell_fill.quantity == Decimal("0.04")
    assert sell_fill.notional_usd == Decimal("100")
    assert sell_fill.fee_usd == Decimal("0.3")

    account = paper.account_snapshot(session)
    # 10,000 - 200 - 0.6 + 100 - 0.3
    assert _money(account["cash_usd"]) == Decimal("9899.1")
    # per position: -0.6 buy fee + (100 - 80 - 0.3) sell result
    assert _money(account["realized_usd"]) == Decimal("19.1")
    assert _money(account["unrealized_usd"]) == Decimal("30")
    assert _money(account["equity_usd"]) == Decimal("10049.1")
    # realized + unrealized reconciles with equity above the genesis balance
    assert _money(account["equity_usd"]) == Decimal("10000") + _money(
        account["realized_usd"]
    ) + _money(account["unrealized_usd"])

    positions = paper.positions(session)
    assert len(positions) == 1
    position = positions[0]
    assert position["chain"] == CHAIN
    assert position["token_address"] == TOKEN
    assert position["symbol"] == SYMBOL
    assert _money(position["quantity"]) == Decimal("0.06")
    assert _money(position["avg_cost_usd"]) == Decimal("2000")
    assert _money(position["mark_usd"]) == Decimal("2500")
    assert _money(position["market_value_usd"]) == Decimal("150")
    assert _money(position["realized_usd"]) == Decimal("19.1")
    assert position["source"]["name"] == "dexscreener"

    # The API schemas accept the read model directly (one PositionView shape).
    from ezchain_api.schemas import PaperAccountOut, PositionOut

    assert PositionOut.model_validate(position).symbol == SYMBOL
    validated = PaperAccountOut.model_validate(account)
    assert validated.equity_usd == account["equity_usd"]
    assert len(validated.positions) == 1

    sums = _group_sums(session)
    assert set(sums) == {"genesis", buy.id, sell.id}
    assert all(total == 0 for total in sums.values())

    cash_entries = session.scalar(
        select(func.count()).select_from(LedgerEntryRow).where(LedgerEntryRow.group_id == sell.id)
    )
    assert cash_entries == 5


def test_sell_is_capped_at_the_full_position(session, settings, dex, jupiter) -> None:
    dex.set(make_snapshot(price=Decimal("1000")))
    buy = place(session, settings, dex, jupiter, make_request(amount_usd="200", key="cap-buy"))
    assert buy.status == "filled"

    sell = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(side="sell", amount_usd="900", key="cap-sell"),
    )
    assert sell.status == "filled"
    fill = session.scalar(select(PaperFillRow).where(PaperFillRow.order_id == sell.id))
    assert fill is not None
    assert fill.quantity == Decimal("0.2")
    assert fill.notional_usd == Decimal("200")

    account = paper.account_snapshot(session)
    # 10,000 - 200 - 0.6 + 200 - 0.6
    assert _money(account["cash_usd"]) == Decimal("9998.8")
    assert paper.positions(session) == []


def test_sell_without_position_is_rejected_and_writes_no_fill(
    session, settings, dex, jupiter
) -> None:
    rejected = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(side="sell", amount_usd="100", key="no-position"),
    )
    assert rejected.status == "rejected"
    assert rejected.reason == "INSUFFICIENT_POSITION"
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 0
    assert session.scalar(select(func.count()).select_from(PaperOrderRow)) == 1
    assert session.scalar(select(func.count()).select_from(LedgerEntryRow)) == 2  # genesis
    assert paper.account_snapshot(session)["cash_usd"] == "10000"


def test_jupiter_quote_price_drives_solana_fills(session, settings, dex, jupiter) -> None:
    solana = "solana"
    mint = "So11111111111111111111111111111111111111112"
    dex.set(
        make_snapshot(
            price=Decimal("2"),
            liquidity=Decimal("1000000"),
            chain=solana,
            address=mint,
        )
    )
    jupiter.price = Decimal("2.5")

    buy = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(amount_usd="100", key="sol-buy", chain=solana, token=mint),
    )
    assert buy.status == "filled"
    buy_fill = session.scalar(select(PaperFillRow).where(PaperFillRow.order_id == buy.id))
    assert buy_fill is not None
    assert buy_fill.quote_kind == "jupiter"
    assert buy_fill.quantity == Decimal("40")
    assert buy_fill.price_usd == Decimal("2.5")
    assert buy_fill.notional_usd == Decimal("100")
    # the buy path also requires a full-size sell route
    assert any(call[0] == "sell" for call in jupiter.calls)

    sell = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(side="sell", amount_usd="100", key="sol-sell", chain=solana, token=mint),
    )
    assert sell.status == "filled"
    sell_fill = session.scalar(select(PaperFillRow).where(PaperFillRow.order_id == sell.id))
    assert sell_fill is not None
    assert sell_fill.quantity == Decimal("40")
    assert sell_fill.price_usd == Decimal("2.5")
    assert sell_fill.notional_usd == Decimal("100")

    sums = _group_sums(session)
    assert all(total == 0 for total in sums.values())


def test_snapshot_is_upserted_with_source_and_observed_at(
    session, settings, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    place(session, settings, dex, jupiter, make_request(amount_usd="100", key="snap-1"))

    from ezchain_api.models import MarketSnapshotRow

    rows = session.scalars(select(MarketSnapshotRow)).all()
    assert len(rows) == 1
    row = rows[0]
    assert (row.chain, row.token_address) == (CHAIN, TOKEN)
    assert row.symbol == SYMBOL
    assert row.price_usd == Decimal("2000")
    assert row.source == "dexscreener"
    assert row.observed_at is not None

    # a retry must not create a second snapshot row
    place(session, settings, dex, jupiter, make_request(amount_usd="100", key="snap-1"))
    assert session.scalar(select(func.count()).select_from(MarketSnapshotRow)) == 1
