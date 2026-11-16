"""The persisted watchlist and its markets filter (GH-33)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_markets_router import FakeDiscovery, make_row

from ezchain_api.deps import get_dexscreener
from ezchain_api.models import WatchlistRow
from ezchain_api.routers import markets as markets_router
from ezchain_api.routers import watchlist as watchlist_router
from ezchain_core import SourceRef

WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


def _client(client_for, **overrides) -> TestClient:
    return client_for(watchlist_router.router, overrides)


def test_add_twice_is_one_row_and_both_posts_return_200(client_for, session: Session) -> None:
    client = _client(client_for)
    body = {"chain": "ethereum", "base_token": WETH, "symbol": "WETH"}

    first = client.post("/api/v1/watchlist", json=body)
    second = client.post("/api/v1/watchlist", json=body)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert session.scalar(select(func.count()).select_from(WatchlistRow)) == 1
    rows = client.get("/api/v1/watchlist").json()
    assert len(rows) == 1
    assert rows[0]["chain"] == "ethereum"


def test_evm_addresses_are_normalized_to_one_key(client_for, session: Session) -> None:
    client = _client(client_for)
    client.post("/api/v1/watchlist", json={"chain": "ethereum", "base_token": WETH.upper()})
    client.post("/api/v1/watchlist", json={"chain": "ethereum", "base_token": WETH.lower()})

    assert session.scalar(select(func.count()).select_from(WatchlistRow)) == 1


def test_unknown_chain_and_token_are_typed_422(client_for, session: Session) -> None:
    client = _client(client_for)

    chain = client.post("/api/v1/watchlist", json={"chain": "nowhere", "base_token": WETH})
    token = client.post(
        "/api/v1/watchlist", json={"chain": "ethereum", "base_token": "not-a-token"}
    )

    assert chain.status_code == 422
    assert chain.json()["detail"]["code"] == "WATCHLIST_CHAIN_UNKNOWN"
    assert token.status_code == 422
    assert token.json()["detail"]["code"] == "WATCHLIST_TOKEN_UNKNOWN"
    assert session.scalar(select(func.count()).select_from(WatchlistRow)) == 0


def test_remove_deletes_only_the_named_pair(client_for, session: Session) -> None:
    client = _client(client_for)
    client.post("/api/v1/watchlist", json={"chain": "ethereum", "base_token": WETH})
    client.post("/api/v1/watchlist", json={"chain": "ethereum", "base_token": USDC})

    removed = client.delete("/api/v1/watchlist", params={"chain": "ethereum", "base_token": WETH})
    assert removed.status_code == 200
    assert removed.json()["removed"] is True
    assert {row["base_token"] for row in client.get("/api/v1/watchlist").json()} == {USDC.lower()}


def test_markets_watchlist_filter_narrows_and_reports_a_token_with_no_snapshot(
    client_for, session: Session
) -> None:
    observed = datetime.now(UTC)
    watch = _client(client_for)
    watch.post("/api/v1/watchlist", json={"chain": "ethereum", "base_token": WETH})
    watch.post(
        "/api/v1/watchlist", json={"chain": "ethereum", "base_token": USDC, "symbol": "USDC"}
    )

    adapter = FakeDiscovery(
        [make_row("ethereum", WETH, "WETH", "10", observed)],
        SourceRef("dexscreener", "https://dexscreener.com", observed),
        None,
    )
    markets = client_for(
        markets_router.router,
        {get_dexscreener: lambda: adapter},
    )

    all_rows = markets.get("/api/v1/markets").json()
    assert len(all_rows["rows"]) == 1  # only WETH has a market row

    narrowed = markets.get("/api/v1/markets", params={"watchlist": "true"}).json()
    assert [row["base_token"] for row in narrowed["rows"]] == [WETH]
    assert narrowed["applied_filters"]["watchlist"] is True
    missing = narrowed["watchlist_missing"]
    assert len(missing) == 1
    assert missing[0]["base_token"] == USDC.lower()
    assert missing[0]["symbol"] == "USDC"
    assert missing[0]["reason"] == "NO_SNAPSHOT"
