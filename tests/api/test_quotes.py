"""Quote route contract: the chain's ladder picks the venue, the API only relays it.

The EVM case runs the real ``KyberSwapClient`` over an ``httpx.MockTransport`` that replays
one recorded Base response, so the test covers the whole path: query parsing → dependency →
router → provider → ``QuoteOut``. The Solana cases keep the pre-existing behaviour: a buy is
only valid with a full-size sell route, and a sell without a quantity is sized from the
mark.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ezchain_adapters import (
    DecimalsResolver,
    KyberSwapClient,
    NoRoute,
    QuoteRequest,
    QuoteRouter,
    get_chain,
)
from ezchain_api.deps import get_quote_router
from ezchain_api.routers import markets as markets_router
from ezchain_core import PairInfo, Quote, SourceRef, TokenSnapshot, utcnow

FIXTURES = Path(__file__).resolve().parents[1] / "adapters" / "fixtures"
WETH = "0x4200000000000000000000000000000000000006"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


@pytest.fixture
def load_fixture() -> Callable[[str], Any]:
    def load(name: str) -> Any:
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    return load


class FakeDex:
    def __init__(self, *, price: Decimal | None = Decimal("2")) -> None:
        self.price = price

    async def get_token(self, chain: str, address: str) -> TokenSnapshot:
        return TokenSnapshot(
            chain=chain,
            address=address,
            symbol="TEST",
            name="Test",
            price_usd=self.price,
            liquidity_usd=Decimal("50000"),
            volume_24h_usd=Decimal("1000"),
            price_change_m5=None,
            price_change_h1=Decimal("1"),
            price_change_h6=None,
            price_change_h24=None,
            pairs=(
                PairInfo(
                    "p",
                    "uniswap",
                    "USDC",
                    self.price or Decimal(0),
                    Decimal("50000"),
                    Decimal("1000"),
                    None,
                ),
            ),
            source=SourceRef("dexscreener", None, utcnow()),
            pair_created_at=datetime(2020, 1, 1, tzinfo=UTC),
        )


class FakeJupiter:
    """Provider-protocol stand-in for Jupiter Lite."""

    kind = "jupiter"

    def __init__(self, *, no_sell: bool = False) -> None:
        self.no_sell = no_sell
        self.calls: list[dict[str, Any]] = []

    def supports(self, chain: Any) -> bool:
        return chain.id == "solana"

    async def quote(self, request: QuoteRequest) -> Quote:
        self.calls.append(
            {
                "side": request.side,
                "quantity": request.quantity,
                "amount_usd": request.amount_usd,
            }
        )
        if request.side == "sell" and self.no_sell:
            raise NoRoute("no route")
        price = Decimal("0.000003")
        if request.side == "buy":
            amount_in, amount_out = request.amount_usd, request.amount_usd / price
        else:
            amount_in = request.quantity or Decimal(0)
            amount_out = amount_in * price
        return Quote(
            chain="solana",
            token_in=USDC,
            token_out=request.token_address,
            side=request.side,
            amount_in=amount_in,
            amount_out=amount_out,
            price=price,
            price_impact_pct=Decimal("0"),
            fee_usd=Decimal("0"),
            kind="jupiter",
            route="Test",
            source=SourceRef("jupiter", None, utcnow()),
        )


def _client(client_for: Any, router: QuoteRouter) -> TestClient:
    return client_for(markets_router.router, {get_quote_router: lambda: router})


def _kyber(load_fixture: Any) -> KyberSwapClient:
    recorded = load_fixture("kyberswap_base_buy.json")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/base/api/v1/routes"
        return httpx.Response(200, json=recorded)

    decimals = DecimalsResolver()
    decimals.remember("base", WETH, 18)
    return KyberSwapClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x"),
        decimals=decimals,
    )


def test_evm_quote_reports_kyberswap_and_its_real_numbers(
    client_for: Any, session: Session, load_fixture: Any
) -> None:
    router = QuoteRouter(providers=[_kyber(load_fixture)], dexscreener=FakeDex())  # type: ignore[arg-type]
    response = _client(client_for, router).get(
        "/api/v1/quotes",
        params={"chain": "base", "token": WETH, "side": "buy", "amount_usd": "100"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "kyberswap"
    assert body["chain"] == "base"
    assert body["token_in"] == USDC
    assert body["token_out"] == WETH
    assert body["amount_out"] == str(Decimal("41783066683609720") / Decimal(10**18))
    assert Decimal(body["price_impact_pct"]) > 0
    assert Decimal(body["fee_usd"]) > 0
    assert body["route"] == "prism-prop"
    assert body["source"]["name"] == "kyberswap"


def test_solana_buy_checks_a_full_size_sell_route(client_for: Any, session: Session) -> None:
    jupiter = FakeJupiter()
    router = QuoteRouter(providers=[jupiter], dexscreener=FakeDex())  # type: ignore[arg-type]
    response = _client(client_for, router).get(
        "/api/v1/quotes",
        params={"chain": "solana", "token": "mint", "side": "buy", "amount_usd": "10"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "jupiter"
    assert [call["side"] for call in jupiter.calls] == ["buy", "sell"]
    assert jupiter.calls[1]["quantity"] == Decimal(body["amount_out"])


def test_solana_buy_without_sell_route_is_409(client_for: Any, session: Session) -> None:
    router = QuoteRouter(
        providers=[FakeJupiter(no_sell=True)],  # type: ignore[list-item]
        dexscreener=FakeDex(),  # type: ignore[arg-type]
    )
    response = _client(client_for, router).get(
        "/api/v1/quotes",
        params={"chain": "solana", "token": "mint", "side": "buy", "amount_usd": "10"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "NO_SELL_ROUTE"


def test_solana_sell_sizes_from_usd_when_quantity_is_omitted(
    client_for: Any, session: Session
) -> None:
    jupiter = FakeJupiter()
    router = QuoteRouter(providers=[jupiter], dexscreener=FakeDex())  # type: ignore[arg-type]
    response = _client(client_for, router).get(
        "/api/v1/quotes",
        params={"chain": "solana", "token": "mint", "side": "sell", "amount_usd": "10"},
    )
    assert response.status_code == 200
    assert jupiter.calls[0]["quantity"] == Decimal("5")


def test_mark_only_chain_returns_paper_mark(client_for: Any, session: Session) -> None:
    router = QuoteRouter(providers=[], dexscreener=FakeDex(price=Decimal("2")))  # type: ignore[arg-type]
    response = _client(client_for, router).get(
        "/api/v1/quotes",
        params={"chain": "blast", "token": "0xtoken", "side": "buy", "amount_usd": "25"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "paper_mark"
    assert body["amount_out"] == "12.5"
    assert body["price_impact_pct"] is None


def test_unknown_chain_is_404(client_for: Any, session: Session) -> None:
    router = QuoteRouter(providers=[], dexscreener=FakeDex())  # type: ignore[arg-type]
    response = _client(client_for, router).get(
        "/api/v1/quotes",
        params={"chain": "nowhere", "token": "0xtoken", "side": "buy", "amount_usd": "10"},
    )
    assert response.status_code == 404


def test_chain_registry_reports_the_primary_provider(client_for: Any, session: Session) -> None:
    from ezchain_api.routers import system as system_router

    client: TestClient = client_for(system_router.router)
    body = client.get("/api/v1/chains").json()
    by_id = {chain["id"]: chain for chain in body}
    assert by_id["base"]["capabilities"]["quote_kind"] == "kyberswap"
    assert by_id["base"]["capabilities"]["quote_providers"] == ["kyberswap", "velora", "lifi"]
    assert by_id["mantle"]["capabilities"]["quote_kind"] == "lifi"
    assert by_id["solana"]["capabilities"]["quote_kind"] == "jupiter"
    assert by_id["blast"]["capabilities"]["quote_kind"] == "paper_mark"
    assert by_id["blast"]["capabilities"]["executable_quotes"] is False
    assert by_id["base"]["capabilities"]["live_execute"] is False
    assert get_chain("base") is not None
