"""Markets router: cursor/offset paging, honest totals and truncation."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
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
    from decimal import Decimal

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


def test_router_uses_full_population_and_reports_round_robin() -> None:
    observed = datetime.now(UTC)
    rows = [
        make_row("ethereum", "0xeth", "ETH", "10", observed),
        make_row("base", "0xbase", "BASE", "500", observed),
        make_row("solana", "0xsol", "SOL", "900000000", observed),
    ]
    adapter = FakeDiscovery(
        rows,
        SourceRef("dexscreener", "https://dexscreener.com", observed),
        [
            ChainCoverage("ethereum", 2, 1, "pairs"),
            ChainCoverage("base", 2, 1, "pairs"),
            ChainCoverage("solana", 4, 1, "pairs"),
        ],
    )
    client = client_for(adapter)

    response = client.get("/api/v1/markets", params={"chain": "all", "sort": "liquidity"})

    assert response.status_code == 200
    body = response.json()
    assert [row["chain"] for row in body["rows"]] == ["ethereum", "base", "solana"]
    assert adapter.calls == [{"chain": "all", "query": None, "sort": "liquidity", "limit": 1000}]
    assert body["total"] == 3
    assert body["has_more"] is False
    assert body["next_offset"] is None
    assert body["next_cursor"] is None
    assert body["offset"] == 0
    assert body["limit"] == 50
    assert body["truncated"] is False
    assert body["population"]["truncated"] is False
    assert body["population"]["rows_discovered"] == 3
    assert body["sort_semantics"] == "per_chain_round_robin"
    assert body["ordered_by"] == "chain_round_robin"
    assert body["population"]["sort_semantics"] == "per_chain_round_robin"


def test_single_chain_browse_is_per_chain_sort_not_global() -> None:
    observed = datetime.now(UTC)
    adapter = FakeDiscovery(
        [make_row("ethereum", "0xeth", "ETH", "10", observed)],
        SourceRef("dexscreener", "https://dexscreener.com", observed),
        [ChainCoverage("ethereum", 2, 1, "pairs")],
    )
    client = client_for(adapter)

    body = client.get("/api/v1/markets", params={"chain": "ethereum", "sort": "trending"}).json()

    # Ranking is within the selected chain. Calling it global would claim an all-chain
    # ordering that no single-chain read produced.
    assert body["sort_semantics"] == "per_chain_sort"
    assert body["ordered_by"] == "per_chain_sort"
    assert body["population"]["sort_semantics"] == "per_chain_sort"


def test_feed_wide_search_is_global_and_reports_no_coverage() -> None:
    observed = datetime.now(UTC)
    adapter = FakeDiscovery(
        [make_row("ethereum", "0xeth", "ETH", "10", observed)],
        SourceRef("dexscreener", "https://dexscreener.com", observed),
        None,
    )
    client = client_for(adapter)

    body = client.get("/api/v1/markets", params={"chain": "all", "q": "ETH"}).json()

    assert body["coverage"] is None
    assert body["sort_semantics"] == "global_sort"
    assert body["ordered_by"] == "global_sort"


def test_coverage_reasons_include_unrequested_and_timeout() -> None:
    observed = datetime.now(UTC)
    adapter = FakeDiscovery(
        [],
        SourceRef("dexscreener", "https://dexscreener.com", observed),
        [
            ChainCoverage("base", 0, 0, "no_seeds"),
            ChainCoverage("solana", 4, 0, "timeout"),
        ],
    )
    client = client_for(adapter)

    response = client.get("/api/v1/markets", params={"chain": "all"})

    assert response.status_code == 200
    coverage = {item["chain"]: item for item in response.json()["coverage"]}
    assert coverage["base"]["reason"] == "no_seeds"
    assert coverage["base"]["requested"] == 0
    assert coverage["solana"]["reason"] == "timeout"
    # Base was never asked, so it is not a scanned chain in the population line.
    assert response.json()["population"]["chains_scanned"] == 1


def test_offset_paging_has_no_duplicates_or_skips() -> None:
    observed = datetime.now(UTC)
    rows = [
        make_row("ethereum", f"0x{index}", f"T{index}", str(1000 - index), observed)
        for index in range(5)
    ]
    adapter = FakeDiscovery(
        rows,
        SourceRef("dexscreener", "https://dexscreener.com", observed),
        [ChainCoverage("ethereum", 2, 5, "pairs")],
    )
    client = client_for(adapter)

    pages = [
        client.get(
            "/api/v1/markets", params={"chain": "ethereum", "limit": 2, "offset": offset}
        ).json()
        for offset in (0, 2, 4, 10)
    ]

    assert [page["total"] for page in pages] == [5, 5, 5, 5]
    assert [[row["symbol"] for row in page["rows"]] for page in pages] == [
        ["T0", "T1"],
        ["T2", "T3"],
        ["T4"],
        [],
    ]
    assert [page["has_more"] for page in pages] == [True, True, False, False]
    assert [page["next_offset"] for page in pages] == [2, 4, None, None]
    paged = [row["symbol"] for page in pages for row in page["rows"]]
    assert paged == [f"T{index}" for index in range(5)]
    assert len(paged) == len(set(paged))


def test_cursor_paging_is_stable_and_idempotent() -> None:
    observed = datetime.now(UTC)
    rows = [
        make_row("ethereum", f"0x{index}", f"T{index}", str(1000 - index), observed)
        for index in range(5)
    ]
    adapter = FakeDiscovery(
        rows,
        SourceRef("dexscreener", "https://dexscreener.com", observed),
        [ChainCoverage("ethereum", 2, 5, "pairs")],
    )
    client = client_for(adapter)

    first = client.get("/api/v1/markets", params={"chain": "ethereum", "limit": 2}).json()
    assert first["has_more"] is True
    second = client.get(
        "/api/v1/markets", params={"chain": "ethereum", "limit": 2, "cursor": first["next_cursor"]}
    ).json()
    third = client.get(
        "/api/v1/markets", params={"chain": "ethereum", "limit": 2, "cursor": second["next_cursor"]}
    ).json()

    assert [[row["symbol"] for row in page["rows"]] for page in (first, second, third)] == [
        ["T0", "T1"],
        ["T2", "T3"],
        ["T4"],
    ]
    # Re-walking with the same cursor is idempotent.
    replay = client.get(
        "/api/v1/markets", params={"chain": "ethereum", "limit": 2, "cursor": first["next_cursor"]}
    ).json()
    assert [row["symbol"] for row in replay["rows"]] == ["T2", "T3"]


def test_cursor_survives_inserted_row_between_pages() -> None:
    observed = datetime.now(UTC)
    base_rows = [
        make_row("ethereum", f"0x{index}", f"T{index}", str(1000 - index), observed)
        for index in range(5)
    ]

    # First page from original dataset.
    adapter = FakeDiscovery(
        list(base_rows),
        SourceRef("dexscreener", "https://dexscreener.com", observed),
        [ChainCoverage("ethereum", 2, 5, "pairs")],
    )
    client = client_for(adapter)
    first = client.get("/api/v1/markets", params={"chain": "ethereum", "limit": 2}).json()
    cursor = first["next_cursor"]

    # A new row inserted ahead of the cursor's anchor position in sort order.
    inserted = make_row("ethereum", "0xnew", "TNEW", "999", observed)
    new_rows = [inserted] + list(base_rows)
    adapter2 = FakeDiscovery(
        new_rows,
        SourceRef("dexscreener", "https://dexscreener.com", observed),
        [ChainCoverage("ethereum", 2, 6, "pairs")],
    )
    client2 = client_for(adapter2)
    page = client2.get(
        "/api/v1/markets", params={"chain": "ethereum", "limit": 2, "cursor": cursor}
    ).json()

    # No duplicate of the first page's rows.
    seen = {row["symbol"] for row in first["rows"]}
    assert seen.isdisjoint({row["symbol"] for row in page["rows"]})


def test_truncated_population_is_explicit() -> None:
    observed = datetime.now(UTC)
    rows = [make_row("ethereum", "0xa", "A", "100", observed)]
    adapter = FakeDiscovery(
        rows,
        SourceRef("dexscreener", "https://dexscreener.com", observed),
        [ChainCoverage("ethereum", 30, 40, "pairs")],
    )
    client = client_for(adapter)

    body = client.get("/api/v1/markets", params={"chain": "ethereum"}).json()

    assert body["population"]["rows_discovered"] == 40
    assert body["population"]["truncated"] is True
    assert body["truncated"] is True
    assert len(body["rows"]) == 1


def test_malformed_cursor_is_rejected() -> None:
    observed = datetime.now(UTC)
    rows = [make_row("ethereum", "0xa", "A", "100", observed)]
    adapter = FakeDiscovery(
        rows, SourceRef("dexscreener", "https://dexscreener.com", observed), None
    )
    client = client_for(adapter)

    response = client.get("/api/v1/markets", params={"chain": "ethereum", "cursor": "not-a-cursor"})
    assert 400 <= response.status_code < 500
    assert response.json()["detail"]["code"] == "INVALID_CURSOR"

    # Expired cursor: valid shape but anchor missing.
    payload = {
        "v": 1,
        "kind": "markets",
        "chain": "ethereum",
        "sort": "trending",
        "q": None,
        "id": "ethereum:missing",
    }
    token = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    response2 = client.get("/api/v1/markets", params={"chain": "ethereum", "cursor": token})
    assert 400 <= response2.status_code < 500
    assert response2.json()["detail"]["code"] == "INVALID_CURSOR"


def test_router_still_rejects_unknown_chain() -> None:
    observed = datetime.now(UTC)
    adapter = FakeDiscovery([], SourceRef("dexscreener", None, observed), None)
    client = client_for(adapter)

    response = client.get("/api/v1/markets", params={"chain": "not-a-chain"})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "CHAIN_NOT_FOUND"
    assert adapter.calls == []
