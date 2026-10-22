"""The manual paper-order route cannot claim strategy attribution."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_api.deps import get_dexscreener, get_jupiter
from ezchain_api.models import PaperOrderRow
from ezchain_api.routers import paper as paper_router
from ezchain_core.types import PairInfo, Quote, SourceRef, TokenSnapshot

CHAIN = "ethereum"
TOKEN = "0x0000000000000000000000000000000000000abc"


class FakeDex:
    async def get_token(self, chain: str, address: str) -> TokenSnapshot:
        observed = datetime.now(UTC)
        return TokenSnapshot(
            chain=chain,
            address=address,
            symbol="TEST",
            name="Test",
            price_usd=Decimal("2"),
            liquidity_usd=Decimal("500000"),
            volume_24h_usd=Decimal("1000000"),
            price_change_m5=Decimal("0"),
            price_change_h1=Decimal("1"),
            price_change_h6=Decimal("0"),
            price_change_h24=Decimal("0"),
            pairs=(
                PairInfo(
                    "0xpair",
                    "uniswap",
                    "USDC",
                    Decimal("2"),
                    Decimal("500000"),
                    Decimal("1000000"),
                    None,
                ),
            ),
            source=SourceRef("dexscreener", None, observed),
            pair_created_at=datetime(2020, 1, 1, tzinfo=UTC),
        )


class FakeJupiter:
    async def quote_paper(self, **kwargs: object) -> tuple[Quote, int, int]:
        raise AssertionError("paper_mark orders must not call Jupiter")


def test_manual_order_with_strategy_id_is_ignored(client_for, session: Session) -> None:
    client: TestClient = client_for(
        paper_router.router,
        {get_dexscreener: lambda: FakeDex(), get_jupiter: lambda: FakeJupiter()},
    )

    response = client.post(
        "/api/v1/paper/orders",
        json={
            "chain": CHAIN,
            "token_address": TOKEN,
            "side": "buy",
            "amount_usd": "25",
            "idempotency_key": "manual-attribution",
            "strategy_id": "strategy-1",
        },
    )

    assert response.status_code == 201
    assert "strategy_id" not in response.json()
    row = session.scalar(select(PaperOrderRow))
    assert row is not None
    assert row.strategy_id is None
