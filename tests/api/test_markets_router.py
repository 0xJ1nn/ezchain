"""Markets router: validated filters, honest totals and stable offset paging.

The adapter owns discovery ordering; the router owns filtering, totals and the slice.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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


def make_row(
    chain: str,
    token: str,
    symbol: str,
    liquidity: str | None,
    observed: datetime,
    *,
    volume: str | None = "1000",
    change_h24: str | None = None,
    created_minutes_ago: int | None = None,
    dex: str = "testdex",
    price: str = "1.5",
) -> MarketRow:
    return MarketRow(
        chain=chain,
        pair_address=f"pair-{chain}-{token}",
        dex=dex,
        base_token=token,
        quote_token="USDC",
        symbol=symbol,
        name=symbol,
        price_usd=Decimal(price),
        liquidity_usd=None if liquidity is None else Decimal(liquidity),
        volume_24h_usd=None if volume is None else Decimal(volume),
        price_change_m5=Decimal("1"),
        price_change_h1=None,
        price_change_h6=None,
        price_change_h24=None if change_h24 is None else Decimal(change_h24),
        pair_created_at=(
            None if created_minutes_ago is None else observed - timedelta(minutes=created_minutes_ago)
        ),
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


def test_router_uses_the_full_population_and_reports_round_robin() -> None:
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
    # The router must not re-sort or re-slice the adapter's discovery order.
    assert [row["chain"] for row in body["rows"]] == ["ethereum", "base", "solana"]
    assert adapter.calls == [
        {"chain": "all", "query": None, "sort": "liquidity", "limit": 1000}
    ]
    assert body["coverage"] == [
        {"chain": "ethereum", "requested": 2, "returned": 1, "reason": "pairs"},
        {"chain": "base", "requested": 2, "returned": 1, "reason": "pairs"},
        {"chain": "solana", "requested": 4, "returned": 1, "reason": "pairs"},
    ]
    assert body["total"] == 3
    assert body["has_more"] is False
    assert body["next_offset"] is None
    assert body["ordered_by"] == "chain_round_robin"
    assert body["sort_semantics"] == "per_chain_round_robin"
    assert body["offset"] == 0
    assert body["limit"] == 50
    assert body["population"] == {
        "chains_scanned": 3,
        "addresses_requested": 8,
        "rows_discovered": 3,
        "rows_after_filters": 3,
        "rows_returned": 3,
        "truncated": False,
        "sort": "liquidity",
        "sort_semantics": "per_chain_round_robin",
    }
    assert body["source"]["observed_at"] == observed.isoformat()
    assert all(
        row["source"]["observed_at"] == body["source"]["observed_at"] for row in body["rows"]
    )


def test_offset_paging_has_no_duplicates_skips_or_invented_pages() -> None:
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
            "/api/v1/markets",
            params={"chain": "ethereum", "limit": 2, "offset": offset},
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
    assert pages[0]["population"]["rows_discovered"] == 5
    assert pages[0]["population"]["rows_returned"] == 2
    assert pages[0]["sort_semantics"] == "global_sort"


def test_filters_are_inclusive_before_limit_and_explain_unknowns() -> None:
    observed = datetime.now(UTC)
    rows = [
        make_row("ethereum", "0xlow", "LOW", "99", observed, volume="500", change_h24="-5"),
        make_row("ethereum", "0xedge", "EDGE", "100", observed, volume="1000", change_h24="0"),
        make_row(
            "ethereum",
            "0xhigh",
            "HIGH",
            "200",
            observed,
            volume="2000",
            change_h24="5",
            dex="Uniswap",
        ),
        make_row("ethereum", "0xnull", "NULL", None, observed, volume=None, change_h24=None),
    ]
    adapter = FakeDiscovery(
        rows,
        SourceRef("dexscreener", "https://dexscreener.com", observed),
        [ChainCoverage("ethereum", 2, 4, "pairs")],
    )
    client = client_for(adapter)

    response = client.get(
        "/api/v1/markets",
        params={
            "chain": "ethereum",
            "limit": 1,
            "min_liquidity_usd": "100",
            "min_volume_24h_usd": "1000",
            "min_change_h24": "0",
            "max_change_h24": "5",
            "dex": "uniswap",
        },
    )

    assert response.status_code == 200
    body = response.json()
    # EDGE is excluded by the DEX filter; HIGH is the only row inside every boundary.
    assert [row["symbol"] for row in body["rows"]] == ["HIGH"]
    assert body["total"] == 1
    assert body["has_more"] is False
    assert body["next_offset"] is None
    assert body["applied_filters"] == {
        "chain": "ethereum",
        "q": None,
        "dex": "uniswap",
        "min_liquidity_usd": "100",
        "min_volume_24h_usd": "1000",
        "min_change_h24": "0",
        "max_change_h24": "5",
        "min_pair_age_minutes": None,
        "max_pair_age_minutes": None,
        "unknown_excluded": {
            "liquidity_usd": 1,
            "volume_24h_usd": 1,
            "price_change_h24": 1,
        },
    }


def test_pair_age_filter_is_inclusive_and_null_safe() -> None:
    observed = datetime.now(UTC)
    rows = [
        make_row("solana", "0xnew", "NEW", "100", observed, created_minutes_ago=30),
        make_row("solana", "0xold", "OLD", "100", observed, created_minutes_ago=119),
        make_row("solana", "0xunknown", "UNKNOWN", "100", observed),
    ]
    adapter = FakeDiscovery(
        rows,
        SourceRef("dexscreener", "https://dexscreener.com", observed),
        None,
    )
    client = client_for(adapter)

    response = client.get(
        "/api/v1/markets",
        params={
            "chain": "solana",
            "q": "S",
            "min_pair_age_minutes": 30,
            "max_pair_age_minutes": 120,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert [row["symbol"] for row in body["rows"]] == ["NEW", "OLD"]
    assert body["total"] == 2
    assert body["applied_filters"]["unknown_excluded"] == {"pair_created_at": 1}
    # Search is not an all-chain census; population does not pretend it scanned chains.
    assert body["sort_semantics"] == "global_sort"
    assert body["population"]["chains_scanned"] is None
    assert body["population"]["addresses_requested"] is None


def test_all_chain_search_is_global_and_reports_no_chain_census() -> None:
    observed = datetime.now(UTC)
    rows = [make_row("solana", "0xbonk", "BONK", "100", observed)]
    adapter = FakeDiscovery(
        rows, SourceRef("dexscreener", "https://dexscreener.com", observed), None
    )
    client = client_for(adapter)

    body = client.get("/api/v1/markets", params={"chain": "all", "q": "bonk"}).json()

    assert adapter.calls == [{"chain": "all", "query": "bonk", "sort": "trending", "limit": 1000}]
    assert body["sort_semantics"] == "global_sort"
    assert body["ordered_by"] == "global_sort"
    assert body["applied_filters"]["q"] == "bonk"
    assert body["population"]["rows_discovered"] == 1


def test_truncated_population_is_explicit_not_silent() -> None:
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
    assert len(body["rows"]) == 1


@pytest.mark.parametrize(
    "params, message",
    [
        ({"min_change_h24": "10", "max_change_h24": "5"}, "min_change_h24"),
        ({"min_pair_age_minutes": "10", "max_pair_age_minutes": "5"}, "min_pair_age_minutes"),
    ],
)
def test_inverted_ranges_are_422_with_a_reason(
    params: dict[str, str], message: str
) -> None:
    observed = datetime.now(UTC)
    adapter = FakeDiscovery(
        [], SourceRef("dexscreener", None, observed), [ChainCoverage("ethereum", 0, 0, "no_pairs")]
    )
    client = client_for(adapter)

    response = client.get("/api/v1/markets", params={"chain": "ethereum", **params})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "INVALID_FILTER"
    assert message in detail["message"]
    assert adapter.calls == []


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 101},
        {"limit": 0},
        {"offset": -1},
        {"min_liquidity_usd": "-1"},
        {"min_pair_age_minutes": "-5"},
    ],
)
def test_invalid_paging_and_filters_are_rejected_by_validation(params: dict[str, int]) -> None:
    observed = datetime.now(UTC)
    adapter = FakeDiscovery(
        [], SourceRef("dexscreener", None, observed), [ChainCoverage("ethereum", 0, 0, "no_pairs")]
    )
    client = client_for(adapter)

    response = client.get("/api/v1/markets", params={"chain": "ethereum", **params})

    assert response.status_code == 422
    assert adapter.calls == []


def test_router_still_rejects_unknown_chain() -> None:
    observed = datetime.now(UTC)
    adapter = FakeDiscovery([], SourceRef("dexscreener", None, observed), None)
    client = client_for(adapter)

    response = client.get("/api/v1/markets", params={"chain": "not-a-chain"})

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "CHAIN_NOT_FOUND"
    assert adapter.calls == []


def test_decimal_text_never_expands_an_unbounded_exponent() -> None:
    # Defence in depth: even a Decimal that bypassed route validation must not become a
    # megabyte string. Out-of-range values stay in short scientific notation.
    assert markets._decimal_text(Decimal("1e1000000")) == "1E+1000000"
    assert markets._decimal_text(Decimal("-1e-1000000")) == "-1E-1000000"
    assert markets._decimal_text(Decimal("1e18")) == "1000000000000000000"
    assert markets._decimal_text(Decimal("0E+1000000")) == "0"


@pytest.mark.parametrize(
    "params",
    [
        {"min_liquidity_usd": "1e1000000"},
        {"min_volume_24h_usd": "1e-1000000"},
        {"min_change_h24": "-1e1000000"},
        {"max_change_h24": "1e-1000000"},
        # Within a Decimal's arithmetic but outside the supported echo range.
        {"min_liquidity_usd": "1e19"},
        {"min_change_h24": "1e-19"},
        {"min_liquidity_usd": "1." + "0" * 40},
    ],
)
def test_unbounded_decimal_filters_are_422_before_discovery(params: dict[str, str]) -> None:
    observed = datetime.now(UTC)
    adapter = FakeDiscovery(
        [], SourceRef("dexscreener", None, observed), [ChainCoverage("ethereum", 0, 0, "no_pairs")]
    )
    client = client_for(adapter)

    response = client.get("/api/v1/markets", params={"chain": "ethereum", **params})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "INVALID_FILTER"
    # The refusal must not reach the provider and must not echo the exponent back expanded.
    assert adapter.calls == []
    assert len(response.content) < 1024


def test_decimal_filters_at_the_boundaries_are_small_plain_text() -> None:
    observed = datetime.now(UTC)
    adapter = FakeDiscovery(
        [make_row("ethereum", "0xa", "A", "100", observed)],
        SourceRef("dexscreener", "https://dexscreener.com", observed),
        [ChainCoverage("ethereum", 2, 1, "pairs")],
    )
    client = client_for(adapter)

    response = client.get(
        "/api/v1/markets",
        params={
            "chain": "ethereum",
            "min_liquidity_usd": "1e18",
            "min_volume_24h_usd": "1e-18",
            "max_change_h24": "1e-18",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["applied_filters"]["min_liquidity_usd"] == "1000000000000000000"
    assert body["applied_filters"]["min_volume_24h_usd"] == "0.000000000000000001"
    assert body["applied_filters"]["max_change_h24"] == "0.000000000000000001"
    assert body["total"] == 0
    assert len(response.content) < 4096
