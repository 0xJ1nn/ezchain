"""Discovery new-pairs router: fail-closed safety, one row per token, honest coverage.

The headline acceptance criterion is that a token with no safety evidence is **never**
listed as tradeable. These tests prove it through the real code paths: the rows come from
``ezchain_adapters.normalize_pair`` and the safety verdict is the adapter's own
``SAFETY_UNKNOWN``, so a hand-built "safe" dict cannot make the test pass.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ezchain_adapters import DexScreenerClient, normalize_pair, snapshot_from_pairs
from ezchain_adapters.dexscreener import BASE as DEXSCREENER_BASE
from ezchain_adapters.dexscreener import SAFETY_UNKNOWN
from ezchain_adapters.ratelimit import TokenBucket
from ezchain_adapters.registry import chain_for_dexscreener
from ezchain_api.config import Settings
from ezchain_api.deps import get_dexscreener
from ezchain_api.routers import discovery
from ezchain_core import MarketRow, SourceRef

FIXTURES = Path(__file__).resolve().parents[1] / "adapters" / "fixtures"
NOW = datetime.now(UTC)

#: "Use the default age" as distinct from an explicit ``None``, which means the provider
#: reported no creation time at all.
DEFAULT_AGE = object()


@pytest.fixture(autouse=True)
def _fresh_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Measure every row's age from the start of its own test, not from import time."""
    monkeypatch.setitem(globals(), "NOW", datetime.now(UTC))


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


def make_row(
    symbol: str,
    *,
    chain: str = "ethereum",
    token: str | None = None,
    pair: str | None = None,
    liquidity: str | None = "50000",
    volume: str | None = "100000",
    h24: str | None = "5",
    pair_created_at: object = DEFAULT_AGE,
    observed: datetime | None = None,
    price: str = "1.5",
) -> MarketRow:
    stamp = NOW if observed is None else observed
    created = (
        (NOW - timedelta(hours=1)) if pair_created_at is DEFAULT_AGE else pair_created_at  # type: ignore[assignment]
    )
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
        price_change_h1=None,
        price_change_h6=None,
        price_change_h24=None if h24 is None else Decimal(h24),
        pair_created_at=created,
        url=None,
        source=SourceRef("dexscreener", "https://dexscreener.com", stamp),
    )


def client_for(adapter: Any) -> TestClient:
    app = FastAPI()
    app.include_router(discovery.router)
    app.dependency_overrides[get_dexscreener] = lambda: adapter
    app.dependency_overrides[discovery.get_settings] = lambda: Settings(
        environment="test",
        data_mode="fixture",
        min_pair_age_minutes=10,
        min_liquidity_usd=Decimal("10000"),
        max_mark_age_seconds=300,
    )
    return TestClient(app)


def get(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get("/api/v1/discovery/new-pairs", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _recorded_pairs() -> list[dict[str, Any]]:
    return json.loads((FIXTURES / "discovery_pairs.json").read_text(encoding="utf-8"))


def _real_rows() -> list[MarketRow]:
    """Rows built by the real adapter normalizer from recorded provider payloads.

    Only the clocks are moved to "now": every number, symbol and pair address is what the
    adapter produced from the recorded provider answer, and the safety verdict is the
    adapter's own ``SAFETY_UNKNOWN`` rather than anything this test assembles.
    """
    rows: list[MarketRow] = []
    for pair in _recorded_pairs():
        row = normalize_pair(pair, NOW - timedelta(minutes=2))
        if row is None:
            continue
        rows.append(
            replace(
                row,
                pair_created_at=NOW - timedelta(hours=2),
                source=SourceRef(
                    "dexscreener", "https://dexscreener.com", NOW - timedelta(minutes=2)
                ),
            )
        )
    return rows


def test_a_row_with_unknown_safety_is_never_tradeable() -> None:
    """The real adapter path cannot produce a tradeable row: it ships SAFETY_UNKNOWN."""
    rows = _real_rows()
    assert rows, "the recorded provider payload must normalize into rows"

    body = get(client_for(FakeDiscovery(rows)), limit=50)

    assert body["rows"], "the fixture rows are young and liquid enough to be listed"
    assert body["tradeable_rows"] == 0
    for row in body["rows"]:
        assert row["tradeable"] is False
        assert row["safety"]["status"] == "unknown"
        assert row["safety"]["reason"] == "SAFETY_NOT_IMPLEMENTED"
        assert row["safety"]["checked_at"] is None
        assert row["not_tradeable_reason"] == "SAFETY_NOT_IMPLEMENTED"


@respx.mock
def test_the_real_adapter_marks_no_row_tradeable() -> None:
    """End to end through the real client: recorded payloads in, no tradeable row out.

    The HTTP layer answers with the recorded provider payload; the client, the normalizer
    and the router are the production ones. Only ``pairCreatedAt`` is moved so the pairs
    fall inside the feed window — a clock, not a safety verdict.
    """
    recent_ms = int((NOW - timedelta(hours=2)).timestamp() * 1000)
    recorded = [
        {**pair, "pairCreatedAt": recent_ms}
        for pair in _recorded_pairs()
        if "pairCreatedAt" in pair
    ]
    by_address: dict[str, list[dict[str, Any]]] = {}
    for pair in recorded:
        for side in ("baseToken", "quoteToken"):
            by_address.setdefault(pair[side]["address"].lower(), []).append(pair)

    def respond(request: httpx.Request) -> httpx.Response:
        addresses = request.url.path.split("/latest/dex/tokens/", 1)[1].split(",")
        selected: dict[str, dict[str, Any]] = {}
        for address in addresses:
            for pair in by_address.get(address.lower(), []):
                selected[pair["pairAddress"]] = pair
        return httpx.Response(200, json={"pairs": list(selected.values())})

    for path in (
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1",
        "/community-takeovers/latest/v1",
    ):
        respx.get(f"{DEXSCREENER_BASE}{path}").mock(return_value=httpx.Response(200, json=[]))
    respx.get(url__regex=rf"^{re.escape(DEXSCREENER_BASE)}/latest/dex/tokens/.+$").mock(
        side_effect=respond
    )
    limiter = TokenBucket(rate_per_second=10_000.0, burst=10_000, clock=lambda: 0.0)
    adapter = DexScreenerClient(
        client=httpx.AsyncClient(base_url=DEXSCREENER_BASE), limiter=limiter
    )

    body = get(client_for(adapter), limit=50)

    assert body["rows"], "the recorded pairs are old enough and liquid enough to be listed"
    assert body["tradeable_rows"] == 0
    assert {row["safety"]["status"] for row in body["rows"]} == {"unknown"}
    assert {row["safety"]["reason"] for row in body["rows"]} == {"SAFETY_NOT_IMPLEMENTED"}
    assert not any(row["tradeable"] for row in body["rows"])


def test_an_absent_safety_check_is_unknown_not_a_pass() -> None:
    """The adapter's own constant is the verdict, and a token page agrees with the feed."""
    pair = next(item for item in _recorded_pairs() if item["chainId"] == "ethereum")
    row = normalize_pair(pair, NOW)
    assert row is not None
    meta = chain_for_dexscreener("ethereum")
    assert meta is not None

    snapshot = snapshot_from_pairs(meta, row.base_token, [pair], observed_at=NOW, now=NOW)

    assert snapshot.safety == SAFETY_UNKNOWN
    listed = replace(row, pair_created_at=NOW - timedelta(hours=2))
    body = get(client_for(FakeDiscovery([listed])))
    assert body["rows"][0]["safety"]["status"] == snapshot.safety["status"]
    assert body["rows"][0]["safety"]["reason"] == snapshot.safety["reason"]
    assert body["rows"][0]["tradeable"] is False


def test_a_token_with_two_pools_appears_once_keeping_the_deepest() -> None:
    rows = [
        make_row("AAA", pair="pair-shallow", liquidity="20000"),
        make_row("AAA", pair="pair-deep", liquidity="900000"),
        make_row("BBB", pair="pair-bbb", liquidity="30000"),
    ]

    body = get(client_for(FakeDiscovery(rows)))

    assert [row["pair_address"] for row in body["rows"]] == ["pair-deep", "pair-bbb"]
    assert {row["token_address"] for row in body["rows"]} == {"token-AAA", "token-BBB"}
    assert body["coverage"]["candidates"] == 3
    assert body["coverage"]["deduped"] == 2
    assert body["coverage"]["dropped"]["duplicate_token"] == 1


def test_each_dropped_row_is_counted_under_its_own_reason() -> None:
    rows = [
        make_row("YOUNG", pair_created_at=NOW - timedelta(minutes=2)),
        make_row("OLD", pair_created_at=NOW - timedelta(days=9)),
        make_row("STALE", observed=NOW - timedelta(minutes=30)),
        make_row("FUTURE", observed=NOW + timedelta(minutes=5)),
        make_row("AGELESS", pair_created_at=None),
        make_row("NOBOOK", liquidity=None),
        make_row("THIN", liquidity="50"),
        make_row("GOOD", pair="pair-good"),
    ]
    body = get(client_for(FakeDiscovery(rows)))

    dropped = body["coverage"]["dropped"]
    assert dropped["pair_too_young"] == 1
    assert dropped["pair_too_old"] == 1
    assert dropped["mark_stale"] == 1
    assert dropped["mark_future"] == 1
    assert dropped["pair_age_unknown"] == 1
    assert dropped["unknown_liquidity"] == 1
    assert dropped["below_liquidity_floor"] == 1
    assert "duplicate_token" not in dropped
    assert [row["symbol"] for row in body["rows"]] == ["GOOD"]
    assert body["coverage"]["candidates"] == 8
    assert body["coverage"]["deduped"] == 8
    assert body["reason"] is None


def test_every_number_carries_its_source_and_age() -> None:
    observed = NOW - timedelta(seconds=42)
    rows = [make_row("AAA", observed=observed, volume=None, h24=None)]

    body = get(client_for(FakeDiscovery(rows)))

    row = body["rows"][0]
    assert row["source"]["name"] == "dexscreener"
    assert row["observed_at"] == observed.isoformat()
    assert row["age_seconds"] >= 40
    for key in ("price_usd", "liquidity_usd", "volume_24h_usd", "price_change_h24_pct"):
        metric = row[key]
        assert metric["source"]["name"] == "dexscreener"
        # Every number names the observation it came from: the row's own provider answer.
        assert metric["source"]["observed_at"] == row["source"]["observed_at"]
        assert metric["age_seconds"] == row["age_seconds"]
    assert row["price_usd"]["known"] is True
    assert row["price_usd"]["value"] == "1.5"
    # Unknown is null and says so; it is never a zero that looks like a measurement.
    assert row["volume_24h_usd"]["value"] is None
    assert row["volume_24h_usd"]["known"] is False
    assert row["price_change_h24_pct"]["value"] is None
    assert row["price_change_h24_pct"]["known"] is False


def test_an_empty_read_returns_a_reason_not_a_row() -> None:
    body = get(client_for(FakeDiscovery([])))

    assert body["rows"] == []
    assert body["reason"] == "NO_MARKET_ROWS"
    assert body["total"] == 0
    assert body["coverage"]["candidates"] == 0
    assert body["coverage"]["deduped"] == 0
    assert body["tradeable_rows"] == 0


def test_a_read_whose_rows_all_fail_returns_a_reason_not_a_row() -> None:
    rows = [make_row("THIN", liquidity="1"), make_row("YOUNG", pair_created_at=NOW)]

    body = get(client_for(FakeDiscovery(rows)))

    assert body["rows"] == []
    assert body["reason"] == "NO_QUALIFIED_MOVERS"
    assert body["coverage"]["dropped"]["below_liquidity_floor"] == 1
    assert body["coverage"]["dropped"]["pair_too_young"] == 1


def test_the_page_is_cut_by_limit_and_the_cut_is_reported() -> None:
    rows = [make_row(f"T{index:02d}") for index in range(1, 8)]

    body = get(client_for(FakeDiscovery(rows)), limit=3)

    assert len(body["rows"]) == 3
    assert body["total"] == 7
    assert body["truncated"] is True
    assert body["coverage"]["truncated"] is True


def test_the_read_is_bounded_and_newest_first() -> None:
    rows = [
        make_row("OLDER", pair="pair-older", pair_created_at=NOW - timedelta(hours=6)),
        make_row("NEWEST", pair="pair-newest", pair_created_at=NOW - timedelta(minutes=20)),
    ]
    adapter = FakeDiscovery(rows)

    body = get(client_for(adapter))

    assert adapter.calls == [
        {"chain": "all", "query": None, "sort": "new", "limit": discovery.DISCOVERY_CANDIDATE_ROWS}
    ]
    assert [row["symbol"] for row in body["rows"]] == ["NEWEST", "OLDER"]
    assert body["applied"]["min_pair_age_seconds"] == 600
    assert body["applied"]["max_pair_age_seconds"] == discovery.DISCOVERY_MAX_PAIR_AGE_SECONDS
    assert body["safety_policy"] == "fail_closed"


def test_an_unknown_chain_is_a_typed_404() -> None:
    client = client_for(FakeDiscovery([]))

    response = client.get("/api/v1/discovery/new-pairs", params={"chain": "not-a-chain"})

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "CHAIN_NOT_FOUND"


def test_the_limit_is_bounded() -> None:
    client = client_for(FakeDiscovery([]))

    assert client.get("/api/v1/discovery/new-pairs", params={"limit": "0"}).status_code == 422
    assert (
        client.get(
            "/api/v1/discovery/new-pairs",
            params={"limit": str(discovery.DISCOVERY_MAX_ROWS + 1)},
        ).status_code
        == 422
    )


def test_a_safety_check_is_the_only_way_to_a_tradeable_row() -> None:
    """A fixture check may prove evidence; the default one never does."""
    from ezchain_api.services.discovery import SafetyEvidence, rank_new_pairs

    rows = [make_row("AAA")]
    bounds = {
        "now": NOW,
        "min_pair_age_seconds": 600,
        "max_pair_age_seconds": discovery.DISCOVERY_MAX_PAIR_AGE_SECONDS,
        "min_liquidity_usd": Decimal("10000"),
        "max_mark_age_seconds": 300,
    }

    default = rank_new_pairs(rows, **bounds)
    verified = rank_new_pairs(
        rows,
        **bounds,
        safety_check=lambda _row: SafetyEvidence(
            status="verified", reason="CHECKED", checked_at=NOW
        ),
    )

    assert default.rows[0].tradeable is False
    assert default.rows[0].not_tradeable_reason == "SAFETY_NOT_IMPLEMENTED"
    assert verified.rows[0].tradeable is True
    assert verified.rows[0].not_tradeable_reason is None
