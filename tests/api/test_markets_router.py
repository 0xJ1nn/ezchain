"""Markets router: the adapter owns ordering, slicing and coverage."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ezchain_adapters import ChainCoverage
from ezchain_api.config import Settings
from ezchain_api.deps import get_dexscreener
from ezchain_api.routers import markets
from ezchain_core import MarketRow, SourceRef


def make_row(chain: str, token: str, symbol: str, liquidity: str, observed: datetime) -> MarketRow:
    return MarketRow(
        chain=chain,
        pair_address=f"pair-{chain}-{token}",
        dex="testdex",
        base_token=token,
        quote_token="USDC",
        symbol=symbol,
        name=symbol,
        price_usd=Decimal("1.5"),
        liquidity_usd=Decimal(liquidity),
        volume_24h_usd=Decimal("1000"),
        price_change_m5=Decimal("1"),
        price_change_h1=None,
        price_change_h6=None,
        price_change_h24=None,
        pair_created_at=None,
        url=None,
        source=SourceRef("dexscreener", "https://dexscreener.com", observed),
    )


class FakeDiscovery:
    def __init__(
        self,
        rows: list[MarketRow],
        source: SourceRef,
        coverage: list[ChainCoverage] | None,
    ) -> None:
        self.rows = rows
        self.source = source
        self.coverage = coverage
        self.calls: list[dict[str, Any]] = []

    async def discover_markets(
        self,
        *,
        chain: str | None = None,
        query: str | None = None,
        sort: str = "trending",
        limit: int = 50,
    ) -> tuple[list[MarketRow], SourceRef, list[ChainCoverage] | None]:
        self.calls.append({"chain": chain, "query": query, "sort": sort, "limit": limit})
        return self.rows, self.source, self.coverage


@pytest.fixture
def settings() -> Settings:
    return Settings(environment="test", data_mode="fixture")


def client_for(adapter: FakeDiscovery) -> TestClient:
    app = FastAPI()
    app.include_router(markets.router)
    app.dependency_overrides[get_dexscreener] = lambda: adapter
    app.dependency_overrides[markets.get_settings] = lambda: Settings(environment="test")
    return TestClient(app)


def test_router_keeps_adapter_order_and_passes_sort_limit() -> None:
    observed = datetime.now(UTC)
    rows = [
        make_row("ethereum", "0xeth", "ETH", "10", observed),
        make_row("base", "0xbase", "BASE", "500", observed),
        make_row("solana", "0xsol", "SOL", "900000000", observed),
    ]
    adapter = FakeDiscovery(
        rows,
        SourceRef("dexscreener", "https://dexscreener.com", observed),
        [ChainCoverage("ethereum", 2, 1, "pairs"), ChainCoverage("base", 2, 1, "pairs")],
    )
    client = client_for(adapter)

    response = client.get(
        "/api/v1/markets", params={"chain": "all", "sort": "liquidity", "limit": 100}
    )

    assert response.status_code == 200
    body = response.json()
    # The router must not re-sort by liquidity or re-slice: the adapter's order is final.
    assert [row["chain"] for row in body["rows"]] == ["ethereum", "base", "solana"]
    assert adapter.calls == [{"chain": "all", "query": None, "sort": "liquidity", "limit": 100}]
    assert body["coverage"] == [
        {"chain": "ethereum", "requested": 2, "returned": 1, "reason": "pairs"},
        {"chain": "base", "requested": 2, "returned": 1, "reason": "pairs"},
    ]
    assert body["ordered_by"] == "chain_round_robin"
    assert body["source"]["observed_at"] == observed.isoformat()
    assert all(
        row["source"]["observed_at"] == body["source"]["observed_at"] for row in body["rows"]
    )


def test_router_still_rejects_unknown_chain() -> None:
    observed = datetime.now(UTC)
    adapter = FakeDiscovery([], SourceRef("dexscreener", None, observed), None)
    client = client_for(adapter)

    response = client.get("/api/v1/markets", params={"chain": "not-a-chain"})

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "CHAIN_NOT_FOUND"
    assert adapter.calls == []
