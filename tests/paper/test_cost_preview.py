"""The pre-trade cost sheet and the journal agree on one cost model.

``preview_cost`` produces the exact fill fee the ledger journals for the same quote,
and the order row records the full sheet before anything fires — for manual and
automated orders alike (one path, one plug), on the intent (order), never on a
strategy. Unknowns stay null: the sheet prints an em dash, never a zero, and a
self-hosted platform fee is a known zero of exactly ``0``.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_adapters import QuoteRequest, QuoteRouter
from ezchain_api.config import Settings
from ezchain_api.models import PaperFillRow
from ezchain_api.services import paper
from ezchain_core import DEFAULT_SLIPPAGE_BPS, CostPreview, Quote, SourceRef, preview_cost, utcnow

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


def _source() -> SourceRef:
    return SourceRef("jupiter", None, utcnow())


def _preview(**kwargs: Any) -> CostPreview:
    inputs: dict[str, Any] = {
        "kind": "jupiter",
        "notional_usd": Decimal("100"),
        "venue_fee_usd": Decimal("0"),
        "price_impact_pct": Decimal("0.01"),
        "amount_out": Decimal("50"),
        "fee_bps": 30,
        "source": _source(),
    }
    inputs.update(kwargs)
    return preview_cost(**inputs)


def test_platform_fee_is_a_known_zero_and_network_fee_is_unknown() -> None:
    preview = _preview()
    assert preview.platform_fee_usd == Decimal(0)
    assert preview.to_dict()["platform_fee_usd"] == "0"
    assert preview.network_fee_usd is None
    assert preview.to_dict()["network_fee_usd"] is None


def test_fill_fee_is_the_journal_rule_venue_wins_or_paper_wins_never_the_sum() -> None:
    assert _preview(venue_fee_usd=Decimal("1.50")).fill_fee_usd == Decimal("1.50")
    assert _preview(venue_fee_usd=Decimal("0.10")).fill_fee_usd == Decimal("0.30")
    assert _preview().fill_fee_usd == Decimal("0.30")


def test_paper_mark_pays_the_flat_fee_and_has_no_window() -> None:
    preview = _preview(kind="paper_mark", venue_fee_usd=Decimal("9.99"))
    assert preview.fill_fee_usd == Decimal("0.30")
    assert preview.slippage_bps is None
    assert preview.worst_case_out is None


def test_worst_case_out_is_inside_the_slippage_window() -> None:
    preview = _preview(amount_out=Decimal("100"))
    assert preview.slippage_bps == DEFAULT_SLIPPAGE_BPS
    assert preview.worst_case_out == Decimal("99.500000")


def test_negative_fee_bps_and_nonpositive_notional_fail() -> None:
    with pytest.raises(ValueError):
        _preview(fee_bps=-1)
    with pytest.raises(ValueError):
        _preview(notional_usd=Decimal(0))


def _place(session: Session, settings: Settings, dex: FakeDexScreener, fee: str) -> Any:
    router = QuoteRouter(providers=[FeeProvider(fee)], dexscreener=dex)  # type: ignore[arg-type]
    dex.set(make_snapshot(price=Decimal("2"), chain=SOLANA, address=MINT))
    return asyncio.run(
        paper.place_order(
            session,
            make_request(amount_usd="100", key=f"preview-fee-{fee}", chain=SOLANA, token=MINT),
            settings,
            dex,  # type: ignore[arg-type]
            router,
        )
    )


def test_filled_order_records_the_preview_that_equals_the_journal_fee(
    session: Session, dex: FakeDexScreener
) -> None:
    settings = make_settings(paper_fee_bps=30)
    order = _place(session, settings, dex, "1.50")
    assert order.status == "filled"
    assert order.quote_json is not None
    assert order.quote_json["cost_preview"]["fill_fee_usd"] == "1.50"
    assert order.quote_json["cost_preview"]["platform_fee_usd"] == "0"
    assert order.quote_json["cost_preview"]["network_fee_usd"] is None
    # The recorded preview is the intent's, and the journal fee equals it exactly.
    fill = session.scalar(select(PaperFillRow).where(PaperFillRow.order_id == order.id))
    assert fill is not None
    assert Decimal(order.quote_json["cost_preview"]["fill_fee_usd"]) == fill.fee_usd


def test_rejected_after_quote_still_records_the_preview(
    session: Session, dex: FakeDexScreener
) -> None:
    settings = make_settings(paper_fee_bps=30, max_order_usd=Decimal("50"))
    order = _place(session, settings, dex, "1.50")
    assert order.status == "rejected"
    assert "ORDER_LIMIT" in (order.reason or "")
    assert order.quote_json is not None
    assert order.quote_json["cost_preview"]["fill_fee_usd"] == "1.50"


def test_preview_on_the_order_never_on_the_strategy(session: Session, dex: FakeDexScreener) -> None:
    settings = make_settings(paper_fee_bps=30)
    order = _place(session, settings, dex, "0")
    assert order.quote_json is not None
    assert order.strategy_id is None
    assert "cost_preview" in order.quote_json
