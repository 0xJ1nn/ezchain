"""Fill fee rule: an executable quote's own cost and the platform fee are never summed.

``paper_mark`` fills pay the flat paper fee. An executable quote already prices a pool or
aggregator fee plus gas, so the fill pays ``max(paper_fee_bps, quote.fee_usd)``: charging
the bps fee on top would bill the same liquidity twice, and charging only the bps fee would
hide a 0.25% LI.FI fee or three dollars of gas.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_adapters import QuoteRequest, QuoteRouter
from ezchain_api.config import Settings
from ezchain_api.models import LedgerEntryRow, PaperFillRow
from ezchain_api.services import paper
from ezchain_core import Quote, SourceRef, utcnow

from .conftest import FakeDexScreener, make_request, make_settings, make_snapshot

SOLANA = "solana"
MINT = "So11111111111111111111111111111111111111112"


class FeeProvider:
    """Jupiter-shaped provider whose quote carries an explicit venue cost."""

    kind = "jupiter"

    def __init__(self, fee: str) -> None:
        self.fee = Decimal(fee)

    def supports(self, chain: Any) -> bool:
        return chain.id == SOLANA

    async def quote(self, request: QuoteRequest) -> Quote:
        price = Decimal("2")
        if request.side == "buy":
            amount_in, amount_out = request.amount_usd, request.amount_usd / price
        else:
            amount_in = request.quantity or Decimal(0)
            amount_out = amount_in * price
        return Quote(
            chain=SOLANA,
            token_in=request.token_address if request.side == "sell" else "USDC",
            token_out="USDC" if request.side == "sell" else request.token_address,
            side=request.side,
            amount_in=amount_in,
            amount_out=amount_out,
            price=price,
            price_impact_pct=Decimal("0.001"),
            fee_usd=self.fee,
            kind="jupiter",
            route="Test",
            source=SourceRef("jupiter", None, utcnow()),
        )


def _place(session: Session, settings: Settings, dex: FakeDexScreener, fee: str) -> Any:
    router = QuoteRouter(providers=[FeeProvider(fee)], dexscreener=dex)  # type: ignore[arg-type]
    dex.set(make_snapshot(price=Decimal("2"), chain=SOLANA, address=MINT))
    return asyncio.run(
        paper.place_order(
            session,
            make_request(amount_usd="100", key=f"fee-{fee}", chain=SOLANA, token=MINT),
            settings,
            dex,  # type: ignore[arg-type]
            router,
        )
    )


def _fee_entries(session: Session) -> Decimal:
    rows = session.scalars(
        select(LedgerEntryRow).where(LedgerEntryRow.account == "fees:USD")
    ).all()
    return sum((row.usd_value for row in rows), Decimal(0))


def test_quote_cost_above_the_platform_fee_wins(session: Session, dex: FakeDexScreener) -> None:
    settings = make_settings(paper_fee_bps=30)
    order = _place(session, settings, dex, "1.50")
    assert order.status == "filled"

    fill = session.scalar(select(PaperFillRow).where(PaperFillRow.order_id == order.id))
    assert fill is not None
    # 30 bps of 100 is 0.30; the quote's own 1.50 is the honest cost of this fill
    assert fill.fee_usd == Decimal("1.50")
    assert _fee_entries(session) == Decimal("1.50")


def test_platform_fee_wins_when_the_quote_is_cheaper(
    session: Session, dex: FakeDexScreener
) -> None:
    settings = make_settings(paper_fee_bps=30)
    order = _place(session, settings, dex, "0.10")
    assert order.status == "filled"

    fill = session.scalar(select(PaperFillRow).where(PaperFillRow.order_id == order.id))
    assert fill is not None
    assert fill.fee_usd == Decimal("0.30")
    assert _fee_entries(session) == Decimal("0.30")


def test_sell_pays_the_same_maximum(session: Session, dex: FakeDexScreener) -> None:
    settings = make_settings(paper_fee_bps=30)
    _place(session, settings, dex, "1.50")
    router = QuoteRouter(providers=[FeeProvider("1.50")], dexscreener=dex)  # type: ignore[arg-type]
    sell = asyncio.run(
        paper.place_order(
            session,
            make_request(side="sell", amount_usd="100", key="fee-sell", chain=SOLANA, token=MINT),
            settings,
            dex,  # type: ignore[arg-type]
            router,
        )
    )
    assert sell.status == "filled"
    fill = session.scalar(select(PaperFillRow).where(PaperFillRow.order_id == sell.id))
    assert fill is not None
    # proceeds are 100 USD: 30 bps is 0.30, the quote cost is 1.50, and both legs are booked
    assert fill.fee_usd == Decimal("1.50")


def test_mark_fill_still_pays_the_flat_paper_fee(session: Session, dex: FakeDexScreener) -> None:
    settings = make_settings(paper_fee_bps=30)
    router = QuoteRouter(providers=[], dexscreener=dex)  # type: ignore[arg-type]
    dex.set(make_snapshot(price=Decimal("2")))
    order = asyncio.run(
        paper.place_order(
            session,
            make_request(amount_usd="100", key="fee-mark"),
            settings,
            dex,  # type: ignore[arg-type]
            router,
        )
    )
    assert order.status == "filled"
    fill = session.scalar(select(PaperFillRow).where(PaperFillRow.order_id == order.id))
    assert fill is not None
    assert fill.quote_kind == "paper_mark"
    assert fill.fee_usd == Decimal("0.30")
