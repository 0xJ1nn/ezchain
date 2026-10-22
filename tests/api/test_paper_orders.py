"""The manual paper-order route cannot claim strategy attribution."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_adapters import QuoteRouter
from ezchain_api.deps import get_dexscreener, get_quote_router
from ezchain_api.models import PaperOrderRow
from ezchain_api.routers import paper as paper_router
from ezchain_core.types import PairInfo, SourceRef, TokenSnapshot

# A chain with no keyless executable quote: this test is about attribution, not venue.
CHAIN = "blast"
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


def test_manual_order_with_strategy_id_is_ignored(client_for, session: Session) -> None:
    dex = FakeDex()
    router = QuoteRouter(providers=[], dexscreener=dex)  # type: ignore[arg-type]
    client: TestClient = client_for(
        paper_router.router,
        {get_dexscreener: lambda: dex, get_quote_router: lambda: router},
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


def _valid_payload() -> dict[str, str]:
    return {
        "chain": CHAIN,
        "token_address": TOKEN,
        "side": "buy",
        "amount_usd": "25",
        "idempotency_key": "validation-key-1",
    }


def test_float_overflow_amount_is_422_not_500(client_for, session: Session) -> None:
    dex = FakeDex()
    router = QuoteRouter(providers=[], dexscreener=dex)  # type: ignore[arg-type]
    client: TestClient = client_for(
        paper_router.router,
        {get_dexscreener: lambda: dex, get_quote_router: lambda: router},
    )
    payload = _valid_payload()
    payload["amount_usd"] = "1e400"

    response = client.post("/api/v1/paper/orders", json=payload)

    assert response.status_code == 422
    assert session.scalar(select(PaperOrderRow)) is None


def test_non_ascii_idempotency_key_is_422(client_for, session: Session) -> None:
    dex = FakeDex()
    router = QuoteRouter(providers=[], dexscreener=dex)  # type: ignore[arg-type]
    client: TestClient = client_for(
        paper_router.router,
        {get_dexscreener: lambda: dex, get_quote_router: lambda: router},
    )
    payload = _valid_payload()
    payload["idempotency_key"] = "idem-ünicode-123"

    response = client.post("/api/v1/paper/orders", json=payload)

    assert response.status_code == 422
    assert session.scalar(select(PaperOrderRow)) is None


def test_json_float_overflow_is_422_not_500(client_for, session: Session) -> None:
    dex = FakeDex()
    router = QuoteRouter(providers=[], dexscreener=dex)  # type: ignore[arg-type]
    client: TestClient = client_for(
        paper_router.router,
        {get_dexscreener: lambda: dex, get_quote_router: lambda: router},
    )
    body = (
        '{"chain": "'
        + CHAIN
        + '", "token_address": "'
        + TOKEN
        + '", "side": "buy", "amount_usd": 1e400, '
        + '"idempotency_key": "overflow-raw-123"}'
    )

    response = client.post(
        "/api/v1/paper/orders",
        content=body.encode(),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert session.scalar(select(PaperOrderRow)) is None
