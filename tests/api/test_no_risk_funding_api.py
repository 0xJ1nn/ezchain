"""The paper order response exposes the no-risk funding audit (GH-106)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from ezchain_adapters import QuoteRouter
from ezchain_api.deps import get_dexscreener, get_quote_router
from ezchain_api.routers import paper as paper_router
from ezchain_core.types import PairInfo, SourceRef, TokenSnapshot

CHAIN = "blast"
TOKEN = "0x0000000000000000000000000000000000000abc"


class FakeDex:
    def __init__(self) -> None:
        self.price = Decimal("2000")

    async def get_token(self, chain: str, address: str, **_: object) -> TokenSnapshot:
        return TokenSnapshot(
            chain=chain,
            address=address,
            symbol="TEST",
            name="Test",
            price_usd=self.price,
            liquidity_usd=Decimal("500000"),
            volume_24h_usd=Decimal("1000000"),
            price_change_m5=Decimal(0),
            price_change_h1=Decimal("1"),
            price_change_h6=Decimal(0),
            price_change_h24=Decimal(0),
            pairs=(
                PairInfo(
                    "0xpair",
                    "uniswap",
                    "USDC",
                    self.price,
                    Decimal("500000"),
                    Decimal("1000000"),
                    None,
                ),
            ),
            source=SourceRef("dexscreener", None, datetime.now(UTC)),
            pair_created_at=datetime(2020, 1, 1, tzinfo=UTC),
        )


def test_funded_order_response_carries_trigger_shortfall_and_cash_delta(
    client_for, session: Session, settings
) -> None:
    dex = FakeDex()
    client = client_for(
        paper_router.router,
        {
            get_dexscreener: lambda: dex,
            get_quote_router: lambda: QuoteRouter(providers=[], dexscreener=dex),  # type: ignore[arg-type]
        },
    )
    settings.paper_no_risk = True
    settings.paper_starting_cash_usd = Decimal("10")

    response = client.post(
        "/api/v1/paper/orders",
        json={
            "chain": CHAIN,
            "token_address": TOKEN,
            "side": "buy",
            "amount_usd": "100",
            "idempotency_key": "api-funding-audit",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["reason"] == "SHORTFALL_FUNDED"
    funding = body["funding"]
    assert funding["trigger"] == "INSUFFICIENT_CASH"
    assert Decimal(funding["shortfall_usd"]) == Decimal("90.3")
    assert Decimal(funding["cash_before_usd"]) == Decimal("10")
    assert Decimal(funding["cash_after_usd"]) == Decimal("100.3")
