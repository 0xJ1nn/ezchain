"""Signals router: server-side ranking, deduplication, filters and honest empty answers.

The read model replaces the browser-side ranking the Signals page used to do. These
tests exercise the router against a fake discovery source, so the ranking, the
exclusion counts and the empty reason codes are all deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ezchain_adapters import UpstreamError
from ezchain_api.config import Settings
from ezchain_api.deps import get_dexscreener
from ezchain_api.routers import signals
from ezchain_core import MarketRow, SourceRef

NOW = datetime.now(UTC)


def make_row(
    symbol: str,
    *,
    chain: str = "solana",
    token: str | None = None,
    pair: str | None = None,
    liquidity: str | None = "500000",
    h1: str | None = None,
    h24: str | None = None,
    observed: datetime | None = None,
    volume: str | None = "1000000",
    price: str = "1.5",
) -> MarketRow:
    stamp = observed if observed is not None else NOW
    return MarketRow(
        chain=chain,
        pair_address=pair or f"pair-{symbol}",
        dex="testdex",
        base_token=token if token is not None else f"token-{symbol}",
        quote_token="USDC",
        symbol=symbol,
        name=symbol,
        price_usd=Decimal(price),
        liquidity_usd=None if liquidity is None else Decimal(liquidity),
        volume_24h_usd=None if volume is None else Decimal(volume),
        price_change_m5=None,
        price_change_h1=None if h1 is None else Decimal(h1),
        price_change_h6=None,
        price_change_h24=None if h24 is None else Decimal(h24),
        pair_created_at=None,
        url=None,
        source=SourceRef("dexscreener", "https://dexscreener.com", stamp),
    )


class FakeDiscovery:
    def __init__(self, rows: list[MarketRow]) -> None:
        self.rows = rows
        self.source = SourceRef("dexscreener", "https://dexscreener.com", NOW)
        self.calls: list[dict[str, Any]] = []

    async def discover_markets(
        self,
        *,
        chain: str | None = None,
        query: str | None = None,
        sort: str = "trending",
        limit: int = 50,
    ) -> tuple[list[MarketRow], SourceRef, None]:
        self.calls.append({"chain": chain, "query": query, "sort": sort, "limit": limit})
        return self.rows, self.source, None


class FailingDiscovery:
    async def discover_markets(
        self,
        *,
        chain: str | None = None,
        query: str | None = None,
        sort: str = "trending",
        limit: int = 50,
    ) -> tuple[list[MarketRow], SourceRef, None]:
        raise UpstreamError("provider is unreachable")


def client_for(adapter: Any) -> TestClient:
    app = FastAPI()
    app.include_router(signals.router)
    app.dependency_overrides[get_dexscreener] = lambda: adapter
    app.dependency_overrides[signals.get_settings] = lambda: Settings(
        environment="test",
        data_mode="fixture",
        min_liquidity_usd=Decimal("10000"),
        max_quote_age_seconds=60,
    )
    return TestClient(app)


def test_movers_are_ranked_by_the_window_change() -> None:
    adapter = FakeDiscovery(
        [
            make_row("AAA", h1="2", h24="40"),
            make_row("BBB", h1="9", h24="10"),
            make_row("CCC", h1="5", h24="90"),
        ]
    )
    client = client_for(adapter)

    response = client.get("/api/v1/signals/movers", params={"window": "h1", "direction": "up"})

    assert response.status_code == 200
    body = response.json()
    assert [row["symbol"] for row in body["rows"]] == ["BBB", "CCC", "AAA"]
    assert [row["rank"] for row in body["rows"]] == [1, 2, 3]
    assert body["rows"][0]["change_pct"] == "9"
    assert body["reason"] is None
    assert body["total"] == 3
    assert body["truncated"] is False


def test_direction_down_returns_the_losers_in_the_same_order() -> None:
    adapter = FakeDiscovery(
        [
            make_row("AAA", h1="-2", h24="-40"),
            make_row("BBB", h1="-9", h24="-10"),
            make_row("CCC", h1="5", h24="90"),
        ]
    )
    client = client_for(adapter)

    body = client.get("/api/v1/signals/movers", params={"window": "h1", "direction": "down"}).json()

    assert [row["symbol"] for row in body["rows"]] == ["BBB", "AAA"]
    assert body["exclusions"]["wrong_direction"] == 1


def test_window_selects_the_change_field() -> None:
    adapter = FakeDiscovery([make_row("AAA", h1="9", h24="-40")])
    client = client_for(adapter)

    up_1h = client.get("/api/v1/signals/movers", params={"window": "h1", "direction": "up"}).json()
    up_24h = client.get(
        "/api/v1/signals/movers", params={"window": "h24", "direction": "up"}
    ).json()
    down_24h = client.get(
        "/api/v1/signals/movers", params={"window": "h24", "direction": "down"}
    ).json()

    assert [row["symbol"] for row in up_1h["rows"]] == ["AAA"]
    assert up_1h["rows"][0]["change_pct"] == "9"
    assert up_24h["rows"] == []
    assert up_24h["reason"] == "NO_QUALIFIED_MOVERS"
    assert [row["symbol"] for row in down_24h["rows"]] == ["AAA"]
    assert down_24h["rows"][0]["change_pct"] == "-40"


def test_one_row_per_token_keeps_the_deepest_pool() -> None:
    adapter = FakeDiscovery(
        [
            make_row("AAA", token="same", pair="pair-shallow", liquidity="1000", h1="9"),
            make_row("AAA", token="same", pair="pair-deep", liquidity="900000", h1="4"),
            make_row("AAA", token="same", pair="pair-middle", liquidity="50000", h1="6"),
        ]
    )
    client = client_for(adapter)

    body = client.get("/api/v1/signals/movers", params={"window": "h1", "direction": "up"}).json()

    assert len(body["rows"]) == 1
    assert body["rows"][0]["pair_address"] == "pair-deep"
    assert body["rows"][0]["change_pct"] == "4"
    assert body["exclusions"]["duplicate_token"] == 2
    assert body["rows_read"] == 3


def test_the_deepest_pool_wins_even_when_another_pool_is_fresher() -> None:
    older = NOW - timedelta(seconds=30)
    adapter = FakeDiscovery(
        [
            make_row(
                "AAA", token="same", pair="pair-shallow", liquidity="50000", h1="9", observed=NOW
            ),
            make_row(
                "AAA", token="same", pair="pair-deep", liquidity="900000", h1="4", observed=older
            ),
        ]
    )
    client = client_for(adapter)

    body = client.get("/api/v1/signals/movers", params={"window": "h1", "direction": "up"}).json()

    assert len(body["rows"]) == 1
    assert body["rows"][0]["pair_address"] == "pair-deep"
    assert body["rows"][0]["change_pct"] == "4"


def test_the_freshest_observation_breaks_a_tie_between_equal_pools() -> None:
    older = NOW - timedelta(seconds=30)
    adapter = FakeDiscovery(
        [
            make_row(
                "AAA", token="same", pair="pair-old", liquidity="900000", h1="9", observed=older
            ),
            make_row(
                "AAA", token="same", pair="pair-new", liquidity="900000", h1="4", observed=NOW
            ),
        ]
    )
    client = client_for(adapter)

    body = client.get("/api/v1/signals/movers", params={"window": "h1", "direction": "up"}).json()

    assert len(body["rows"]) == 1
    assert body["rows"][0]["pair_address"] == "pair-new"
    assert body["rows"][0]["change_pct"] == "4"


def test_the_liquidity_floor_excludes_thin_pools_and_counts_them() -> None:
    adapter = FakeDiscovery(
        [
            make_row("AAA", liquidity="900000", h1="5"),
            make_row("BBB", liquidity="500", h1="9"),
        ]
    )
    client = client_for(adapter)

    body = client.get(
        "/api/v1/signals/movers",
        params={"window": "h1", "direction": "up", "min_liquidity_usd": "10000"},
    ).json()

    assert [row["symbol"] for row in body["rows"]] == ["AAA"]
    assert body["exclusions"]["below_liquidity_floor"] == 1
    assert body["applied"]["min_liquidity_usd"] == "10000"


def test_unknown_liquidity_is_excluded_not_treated_as_zero() -> None:
    adapter = FakeDiscovery(
        [
            make_row("AAA", liquidity=None, h1="5"),
            make_row("BBB", liquidity="500", h1="9"),
        ]
    )
    client = client_for(adapter)

    body = client.get(
        "/api/v1/signals/movers",
        params={"window": "h1", "direction": "up", "min_liquidity_usd": "10000"},
    ).json()

    assert body["rows"] == []
    assert body["exclusions"]["unknown_liquidity"] == 1
    assert body["exclusions"]["below_liquidity_floor"] == 1
    assert body["reason"] == "NO_QUALIFIED_MOVERS"


def test_unknown_change_is_excluded_and_counted() -> None:
    adapter = FakeDiscovery([make_row("AAA", h1=None, h24="9")])
    client = client_for(adapter)

    body = client.get("/api/v1/signals/movers", params={"window": "h1", "direction": "up"}).json()

    assert body["rows"] == []
    assert body["exclusions"]["unknown_change"] == 1
    assert body["reason"] == "NO_QUALIFIED_MOVERS"


def test_a_stale_mark_is_excluded_and_counted() -> None:
    stale = NOW - timedelta(seconds=600)
    adapter = FakeDiscovery(
        [
            make_row("AAA", h1="9", observed=stale),
            make_row("BBB", h1="4", observed=NOW),
        ]
    )
    client = client_for(adapter)

    body = client.get(
        "/api/v1/signals/movers",
        params={"window": "h1", "direction": "up", "max_age_seconds": "60"},
    ).json()

    assert [row["symbol"] for row in body["rows"]] == ["BBB"]
    assert body["exclusions"]["stale"] == 1
    assert body["rows"][0]["age_seconds"] <= 15


def test_every_row_carries_its_source_observation_and_age() -> None:
    adapter = FakeDiscovery([make_row("AAA", h1="5", observed=NOW - timedelta(seconds=12))])
    client = client_for(adapter)

    body = client.get("/api/v1/signals/movers", params={"window": "h1", "direction": "up"}).json()

    row = body["rows"][0]
    assert row["source"]["name"] == "dexscreener"
    assert row["observed_at"] == (NOW - timedelta(seconds=12)).isoformat()
    assert 5 <= row["age_seconds"] <= 45
    assert body["source"]["name"] == "dexscreener"
    assert body["stale"] is False


def test_an_empty_market_read_returns_a_reason_not_a_mover() -> None:
    adapter = FakeDiscovery([])
    client = client_for(adapter)

    body = client.get("/api/v1/signals/movers", params={"window": "h1", "direction": "up"}).json()

    assert body["rows"] == []
    assert body["reason"] == "NO_MARKET_ROWS"
    assert body["total"] == 0
    assert body["rows_read"] == 0


def test_the_result_count_is_capped_and_the_cut_is_reported() -> None:
    rows = [make_row(f"T{index:02d}", h1=str(index)) for index in range(1, 21)]
    adapter = FakeDiscovery(rows)
    client = client_for(adapter)

    body = client.get(
        "/api/v1/signals/movers",
        params={"window": "h1", "direction": "up", "limit": "10"},
    ).json()

    assert len(body["rows"]) == 10
    assert body["total"] == 20
    assert body["truncated"] is True
    assert body["limit"] == 10
    assert body["rows"][0]["symbol"] == "T20"
    assert body["rows"][-1]["symbol"] == "T11"


def test_the_limit_is_bounded_by_the_documented_maximum() -> None:
    client = client_for(FakeDiscovery([make_row("AAA", h1="5")]))

    accepted = client.get("/api/v1/signals/movers", params={"limit": signals.SIGNALS_MAX_ROWS})
    refused = client.get("/api/v1/signals/movers", params={"limit": signals.SIGNALS_MAX_ROWS + 1})

    assert accepted.status_code == 200
    assert refused.status_code == 422


def test_the_market_read_is_bounded_and_its_sort_follows_the_window() -> None:
    adapter = FakeDiscovery([make_row("AAA", h1="5", h24="9")])
    client = client_for(adapter)

    client.get("/api/v1/signals/movers", params={"window": "h1"})
    client.get("/api/v1/signals/movers", params={"window": "h24"})

    assert adapter.calls[0]["sort"] == "trending"
    assert adapter.calls[1]["sort"] == "change_h24"
    assert adapter.calls[0]["limit"] == signals.SIGNALS_CANDIDATE_ROWS


def test_an_unknown_chain_is_refused() -> None:
    client = client_for(FakeDiscovery([make_row("AAA", h1="5")]))

    response = client.get("/api/v1/signals/movers", params={"chain": "nope"})

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "CHAIN_NOT_FOUND"


def test_an_unbounded_floor_is_refused() -> None:
    client = client_for(FakeDiscovery([make_row("AAA", h1="5")]))

    response = client.get("/api/v1/signals/movers", params={"min_liquidity_usd": "1e1000000"})

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_FILTER"


def test_a_provider_failure_surfaces_the_adapter_reason() -> None:
    client = client_for(FailingDiscovery())

    response = client.get("/api/v1/signals/movers")

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "UPSTREAM_ERROR"


def test_the_exclusion_counts_reconcile_with_the_rows_read() -> None:
    adapter = FakeDiscovery(
        [
            make_row("AAA", h1="5"),
            make_row("BBB", h1="4"),
            make_row("CCC", h1="3"),
            make_row("DDD", h1=None),
            make_row("EEE", liquidity=None, h1="2"),
            make_row("FFF", liquidity="10", h1="1"),
            make_row("GGG", h1="-5"),
            make_row("AAA", token="token-AAA", pair="pair-two", liquidity="900000", h1="6"),
            make_row("NOTOKEN", token="", h1="7"),
        ]
    )
    client = client_for(adapter)

    body = client.get(
        "/api/v1/signals/movers",
        params={"window": "h1", "direction": "up", "min_liquidity_usd": "10000"},
    ).json()

    counted = sum(body["exclusions"].values())
    assert counted + len(body["rows"]) == body["rows_read"]
    assert body["rows_read"] == 9
    assert [row["symbol"] for row in body["rows"]] == ["AAA", "BBB", "CCC"]
    assert body["exclusions"]["unknown_token"] == 1
    assert body["exclusions"]["duplicate_token"] == 1
    assert body["exclusions"]["unknown_change"] == 1
    assert body["exclusions"]["unknown_liquidity"] == 1
    assert body["exclusions"]["below_liquidity_floor"] == 1
    assert body["exclusions"]["wrong_direction"] == 1


def test_rows_without_a_token_are_never_linked() -> None:
    adapter = FakeDiscovery([make_row("NOTOKEN", token="", h1="7")])
    client = client_for(adapter)

    body = client.get("/api/v1/signals/movers", params={"window": "h1", "direction": "up"}).json()

    assert body["rows"] == []
    assert body["exclusions"]["unknown_token"] == 1


@pytest.mark.parametrize("window", ["h1", "h24"])
def test_the_response_names_the_window_and_the_direction(window: str) -> None:
    client = client_for(FakeDiscovery([make_row("AAA", h1="5", h24="6")]))

    body = client.get("/api/v1/signals/movers", params={"window": window, "direction": "up"}).json()

    assert body["window"] == window
    assert body["direction"] == "up"
    assert body["applied"]["window"] == window
    assert body["applied"]["direction"] == "up"
