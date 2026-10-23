"""DexScreener contract tests: normalization, discovery, cache, errors.

All HTTP is mocked with respx. Fixtures are trimmed recordings; nothing here reaches the
network.
"""

from __future__ import annotations

import asyncio
import copy
import re
from collections.abc import Callable
from datetime import UTC
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx

from ezchain_adapters import DexScreenerClient, NotFound, RateLimited, UpstreamError
from ezchain_adapters.dexscreener import (
    BASE,
    MAX_BATCH,
    MAX_DISCOVERY_ADDRESSES,
    normalize_pair,
    snapshot_from_pairs,
)
from ezchain_adapters.registry import CHAINS, get_chain
from ezchain_adapters.seeds import SEED_TOKENS

ETH_WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
TOKENS_URL = rf"^{re.escape(BASE)}/latest/dex/tokens/.+$"


def _valid_pair() -> dict[str, Any]:
    return {
        "chainId": "ethereum",
        "dexId": "uniswap",
        "pairAddress": "0xpair",
        "baseToken": {"address": "0xbase", "symbol": "WETH", "name": "Wrapped Ether"},
        "quoteToken": {"address": "0xquote", "symbol": "USDC", "name": "USD Coin"},
        "priceUsd": "2000.5",
        "liquidity": {"usd": 100_000},
        "volume": {"h24": 25_000},
        "priceChange": {"m5": 0.5, "h1": 1.5, "h6": 2.5, "h24": 3.5},
        "pairCreatedAt": 1_700_000_000_000,
        "url": "https://dexscreener.com/ethereum/0xpair",
    }


def _addresses(request: httpx.Request) -> list[str]:
    marker = "/latest/dex/tokens/"
    return request.url.path.split(marker, 1)[1].split(",")


def _hint_pair(item: dict[str, Any], liquidity: float) -> dict[str, Any]:
    token = item["tokenAddress"]
    return {
        "chainId": item["chainId"],
        "dexId": "raydium",
        "pairAddress": f"pair-{token}",
        "baseToken": {"address": token, "symbol": "HINT", "name": "Hint token"},
        "quoteToken": {
            "address": "So11111111111111111111111111111111111111112",
            "symbol": "SOL",
            "name": "Wrapped SOL",
        },
        "priceUsd": "1.5",
        "liquidity": {"usd": liquidity},
        "volume": {"h24": 10_000},
        "priceChange": {"m5": 1.0, "h1": 2.0},
        "pairCreatedAt": 1_700_000_000_000,
        "url": "https://dexscreener.com/solana/hint",
    }


def test_public_rate_budget_is_respected() -> None:
    assert DexScreenerClient.rate_per_second * 60 == 300
    assert 1 <= DexScreenerClient.burst <= 30
    assert MAX_BATCH == 30
    assert MAX_DISCOVERY_ADDRESSES == MAX_BATCH * 4


def test_normalize_pair_maps_every_field(load_fixture: Callable[[str], Any]) -> None:
    recorded = load_fixture("discovery_pairs.json")
    pair = next(item for item in recorded if item["chainId"] == "ethereum")
    row = normalize_pair(pair)
    assert row is not None
    assert row.chain == "ethereum"
    assert row.pair_address == pair["pairAddress"]
    assert row.dex == pair["dexId"]
    assert row.base_token == pair["baseToken"]["address"]
    assert row.quote_token == pair["quoteToken"]["address"]
    assert row.symbol == pair["baseToken"]["symbol"]
    assert row.name == pair["baseToken"]["name"]
    assert row.price_usd == Decimal(str(pair["priceUsd"]))
    assert row.liquidity_usd == Decimal(str(pair["liquidity"]["usd"]))
    assert row.volume_24h_usd == Decimal(str(pair["volume"]["h24"]))
    raw_m5 = (pair.get("priceChange") or {}).get("m5")
    assert row.price_change_m5 == (None if raw_m5 is None else Decimal(str(raw_m5)))
    assert row.pair_created_at is not None
    assert row.pair_created_at.tzinfo is not None
    assert row.url == pair["url"]
    assert row.source.name == "dexscreener"
    assert row.source.observed_at.tzinfo is UTC


def test_normalize_pair_defaults_missing_optional_fields() -> None:
    pair = _valid_pair()
    for key in ("liquidity", "volume", "priceChange", "pairCreatedAt", "url"):
        del pair[key]
    row = normalize_pair(pair)
    assert row is not None
    assert row.liquidity_usd is None
    assert row.volume_24h_usd is None
    assert row.price_change_m5 is None
    assert row.pair_created_at is None
    assert row.url is None


@pytest.mark.parametrize("value", [None, "not-a-pair", 42, ["pair"]])
def test_normalize_pair_rejects_non_mappings(value: object) -> None:
    assert normalize_pair(value) is None


@pytest.mark.parametrize(
    "pair_update",
    [
        {"chainId": "not-a-chain"},
        {"chainId": None},
        {"pairAddress": ""},
        {"pairAddress": None},
        {"priceUsd": None},
        {"priceUsd": ""},
        {"priceUsd": "0"},
        {"priceUsd": "-1"},
        {"priceUsd": "NaN"},
        {"priceUsd": "Infinity"},
        {"priceUsd": "oops"},
    ],
)
def test_normalize_pair_rejects_unknown_chain_or_bad_price(
    pair_update: dict[str, object],
) -> None:
    assert normalize_pair({**_valid_pair(), **pair_update}) is None


def test_normalize_pair_never_turns_non_finite_metrics_into_numbers() -> None:
    pair = _valid_pair()
    pair["liquidity"] = {"usd": "NaN"}
    pair["volume"] = {"h24": "Infinity"}
    pair["priceChange"] = {"m5": "NaN"}
    row = normalize_pair(pair)
    assert row is not None
    assert row.liquidity_usd is None
    assert row.volume_24h_usd is None
    assert row.price_change_m5 is None


def test_normalize_pair_ignores_malformed_timestamp() -> None:
    pair = _valid_pair()
    pair["pairCreatedAt"] = "1700000000000"
    row = normalize_pair(pair)
    assert row is not None
    assert row.pair_created_at is None


def test_snapshot_best_pair_uses_liquidity_and_filters_foreign_chains(
    load_fixture: Callable[[str], Any],
) -> None:
    pairs = load_fixture("token_pairs_ethereum_weth.json")
    foreign = copy.deepcopy(pairs[0])
    foreign["chainId"] = "solana"
    foreign["pairAddress"] = "solana-pair"
    duplicate = copy.deepcopy(pairs[0])
    duplicate["liquidity"]["usd"] = 999_999_999
    meta = get_chain("ethereum")
    assert meta is not None
    snapshot = snapshot_from_pairs(meta, ETH_WETH, [*pairs, foreign, duplicate])
    assert len(snapshot.pairs) == len(pairs)
    assert snapshot.liquidity_usd == Decimal("999999999")
    assert snapshot.symbol == duplicate["baseToken"]["symbol"]
    assert snapshot.address == ETH_WETH
    assert snapshot.safety == {"status": "unknown", "reason": "SAFETY_NOT_IMPLEMENTED"}
    assert snapshot.stale is False
    assert {pair.pair_address for pair in snapshot.pairs} == {pair["pairAddress"] for pair in pairs}


def test_snapshot_ignores_pairs_from_other_chains() -> None:
    foreign = {**_valid_pair(), "chainId": "solana", "pairAddress": "foreign"}
    meta = get_chain("ethereum")
    assert meta is not None
    with pytest.raises(NotFound):
        snapshot_from_pairs(meta, ETH_WETH, [foreign])


@respx.mock
async def test_get_token_selects_the_deepest_pair(
    dexscreener: DexScreenerClient, load_fixture: Callable[[str], Any]
) -> None:
    pairs = load_fixture("token_pairs_ethereum_weth.json")
    duplicate = copy.deepcopy(pairs[0])
    duplicate["liquidity"]["usd"] = 999_999_999
    respx.get(f"{BASE}/token-pairs/v1/ethereum/{ETH_WETH}").mock(
        return_value=httpx.Response(200, json=[*pairs, duplicate])
    )
    async with dexscreener as client:
        snapshot = await client.get_token("ethereum", ETH_WETH)
    assert len(snapshot.pairs) == len(pairs)
    assert snapshot.liquidity_usd == Decimal("999999999")
    assert snapshot.price_usd == Decimal(str(duplicate["priceUsd"]))


@respx.mock
async def test_get_token_raises_not_found_for_empty_pairs(
    dexscreener: DexScreenerClient,
) -> None:
    respx.get(f"{BASE}/token-pairs/v1/ethereum/{ETH_WETH}").mock(
        return_value=httpx.Response(200, json=[])
    )
    async with dexscreener as client:
        with pytest.raises(NotFound):
            await client.get_token("ethereum", ETH_WETH)


@respx.mock
async def test_get_token_rejects_unknown_chain_without_a_request(
    dexscreener: DexScreenerClient,
) -> None:
    async with dexscreener as client:
        with pytest.raises(NotFound):
            await client.get_token("not-a-chain", ETH_WETH)


@respx.mock
async def test_list_markets_dedupes_filters_and_limits(
    dexscreener: DexScreenerClient, load_fixture: Callable[[str], Any]
) -> None:
    pairs = load_fixture("token_pairs_ethereum_weth.json")
    duplicate = copy.deepcopy(pairs[0])
    duplicate["liquidity"]["usd"] = 900_000_000
    foreign = copy.deepcopy(pairs[0])
    foreign["chainId"] = "solana"
    foreign["pairAddress"] = "solana-pair"
    unindexed = copy.deepcopy(pairs[0])
    unindexed["chainId"] = "not-indexed"
    unindexed["pairAddress"] = "unindexed-pair"
    respx.get(f"{BASE}/latest/dex/search").mock(
        return_value=httpx.Response(200, json={"pairs": [*pairs, duplicate, foreign, unindexed]})
    )
    async with dexscreener as client:
        rows, source = await client.list_markets(
            query="WETH", chain="ethereum", sort="liquidity", limit=2
        )
        all_rows, _ = await client.list_markets(query="WETH", chain="all", limit=50)
    assert len(rows) == 2
    assert all(row.chain == "ethereum" for row in rows)
    assert [row.liquidity_usd for row in rows] == sorted(
        (row.liquidity_usd for row in rows), reverse=True
    )
    assert rows[0].liquidity_usd == Decimal("900000000")
    assert sum(row.pair_address == pairs[0]["pairAddress"] for row in rows) == 1
    assert "solana" in {row.chain for row in all_rows}
    assert "not-indexed" not in {row.chain for row in all_rows}
    assert source.name == "dexscreener"
    assert source.observed_at.tzinfo is UTC


@respx.mock
async def test_list_markets_limit_zero_makes_no_request(
    dexscreener: DexScreenerClient,
) -> None:
    async with dexscreener as client:
        rows, source = await client.list_markets(chain="all", limit=0)
    assert rows == []
    assert source.name == "dexscreener"


@respx.mock
async def test_list_markets_caches_repeated_requests(
    dexscreener: DexScreenerClient, load_fixture: Callable[[str], Any]
) -> None:
    pairs = load_fixture("token_pairs_ethereum_weth.json")
    route = respx.get(f"{BASE}/latest/dex/search").mock(
        return_value=httpx.Response(200, json={"pairs": pairs})
    )
    async with dexscreener as client:
        first, _ = await client.list_markets(query="WETH", chain="ethereum", limit=5)
        second, _ = await client.list_markets(query="WETH", chain="ethereum", limit=5)
    assert route.call_count == 1
    assert [(row.chain, row.pair_address, row.price_usd) for row in first] == [
        (row.chain, row.pair_address, row.price_usd) for row in second
    ]


@respx.mock
async def test_inflight_identical_requests_are_deduplicated(
    dexscreener: DexScreenerClient,
) -> None:
    calls = 0

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return httpx.Response(200, json={"pairs": []})

    route = respx.get(f"{BASE}/latest/dex/search").mock(side_effect=slow)
    async with dexscreener as client:
        first, second = await asyncio.gather(client.search("WETH"), client.search("WETH"))
    assert calls == 1
    assert route.call_count == 1
    assert first == second == []


@respx.mock
async def test_tokens_respects_the_30_token_batch_and_budget(
    dexscreener: DexScreenerClient,
) -> None:
    batches: list[list[str]] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        batches.append(_addresses(request))
        return httpx.Response(200, json={"pairs": []})

    respx.get(url__regex=TOKENS_URL).mock(side_effect=respond)
    async with dexscreener as client:
        await client.tokens([f"token{i}" for i in range(65)])
        await client.tokens([f"extra{i}" for i in range(200)])
    assert [len(batch) for batch in batches[:3]] == [30, 30, 5]
    assert all(len(batch) <= MAX_BATCH for batch in batches)
    assert sum(len(batch) for batch in batches[3:]) == MAX_DISCOVERY_ADDRESSES


@respx.mock
async def test_chain_all_discovery_is_not_solana_only(
    dexscreener: DexScreenerClient, load_fixture: Callable[[str], Any]
) -> None:
    recorded = load_fixture("discovery_pairs.json")
    by_address: dict[str, list[dict[str, Any]]] = {}
    for pair in recorded:
        for side in ("baseToken", "quoteToken"):
            by_address.setdefault(pair[side]["address"].lower(), []).append(pair)
    batches: list[list[str]] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        addresses = _addresses(request)
        batches.append(addresses)
        selected: dict[str, dict[str, Any]] = {}
        for address in addresses:
            for pair in by_address.get(address.lower(), []):
                selected[pair["pairAddress"]] = pair
        return httpx.Response(200, json={"pairs": list(selected.values())})

    for path, name in [
        ("/token-profiles/latest/v1", "profiles_solana.json"),
        ("/token-boosts/latest/v1", "boosts_latest_solana.json"),
        ("/token-boosts/top/v1", "boosts_top_solana.json"),
        ("/community-takeovers/latest/v1", "takeovers_solana.json"),
    ]:
        respx.get(f"{BASE}{path}").mock(return_value=httpx.Response(200, json=load_fixture(name)))
    respx.get(url__regex=TOKENS_URL).mock(side_effect=respond)

    async with dexscreener as client:
        rows, _ = await client.list_markets(chain="all", limit=100)

    requested = [address for batch in batches for address in batch]
    assert requested
    assert all(len(batch) <= MAX_BATCH for batch in batches)
    # One token lookup per registered chain; the hint feeds are four more requests.
    assert len(batches) <= len(CHAINS)
    assert any(address.lower() == ETH_WETH.lower() for address in requested)
    chains = {row.chain for row in rows}
    assert len(chains) >= 12
    assert {"ethereum", "bsc", "sui", "aptos", "ton", "tron", "near", "celo", "metis"} <= chains
    assert {"solana"} != chains


@respx.mock
async def test_discovery_aggregates_profiles_boosts_and_takeovers(
    dexscreener: DexScreenerClient, load_fixture: Callable[[str], Any]
) -> None:
    feeds = {
        "/token-profiles/latest/v1": "profiles_solana.json",
        "/token-boosts/latest/v1": "boosts_latest_solana.json",
        "/token-boosts/top/v1": "boosts_top_solana.json",
        "/community-takeovers/latest/v1": "takeovers_solana.json",
    }
    hint_tokens: dict[str, dict[str, Any]] = {}
    routes = {}
    for path, name in feeds.items():
        items = load_fixture(name)
        routes[path] = respx.get(f"{BASE}{path}").mock(return_value=httpx.Response(200, json=items))
        for item in items:
            hint_tokens[item["tokenAddress"].lower()] = item

    async def respond(request: httpx.Request) -> httpx.Response:
        selected = []
        for address in _addresses(request):
            item = hint_tokens.get(address.lower())
            if item is not None:
                selected.append(_hint_pair(item, 50_000_000))
        return httpx.Response(200, json={"pairs": selected})

    respx.get(url__regex=TOKENS_URL).mock(side_effect=respond)
    async with dexscreener as client:
        rows, _ = await client.list_markets(chain="all", limit=200)

    for route in routes.values():
        assert route.call_count == 1
    assert {row.base_token.lower() for row in rows} >= set(hint_tokens)


@respx.mock
async def test_discovery_survives_a_failing_hint_feed(
    dexscreener: DexScreenerClient, load_fixture: Callable[[str], Any]
) -> None:
    profiles = respx.get(f"{BASE}/token-profiles/latest/v1").mock(return_value=httpx.Response(503))
    for path, name in [
        ("/token-boosts/latest/v1", "boosts_latest_solana.json"),
        ("/token-boosts/top/v1", "boosts_top_solana.json"),
        ("/community-takeovers/latest/v1", "takeovers_solana.json"),
    ]:
        respx.get(f"{BASE}{path}").mock(return_value=httpx.Response(200, json=load_fixture(name)))
    recorded = load_fixture("discovery_pairs.json")

    async def respond(request: httpx.Request) -> httpx.Response:
        addresses = {address.lower() for address in _addresses(request)}
        selected = [
            pair
            for pair in recorded
            if pair["baseToken"]["address"].lower() in addresses
            or pair["quoteToken"]["address"].lower() in addresses
        ]
        return httpx.Response(200, json={"pairs": selected})

    respx.get(url__regex=TOKENS_URL).mock(side_effect=respond)
    async with dexscreener as client:
        rows, _ = await client.list_markets(chain="all", limit=50)
    assert profiles.call_count == 1
    assert rows


@respx.mock
async def test_single_chain_discovery_drops_other_chain_hints(
    dexscreener: DexScreenerClient, load_fixture: Callable[[str], Any]
) -> None:
    respx.get(f"{BASE}/token-profiles/latest/v1").mock(
        return_value=httpx.Response(200, json=load_fixture("profiles_solana.json"))
    )
    for path in ("/token-boosts/latest/v1", "/token-boosts/top/v1"):
        respx.get(f"{BASE}{path}").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{BASE}/community-takeovers/latest/v1").mock(
        return_value=httpx.Response(200, json=[])
    )
    recorded = load_fixture("discovery_pairs.json")
    bsc_pairs = [pair for pair in recorded if pair["chainId"] == "bsc"]
    batches: list[list[str]] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        addresses = _addresses(request)
        batches.append(addresses)
        wanted = {address.lower() for address in addresses}
        selected = [
            pair
            for pair in bsc_pairs
            if pair["baseToken"]["address"].lower() in wanted
            or pair["quoteToken"]["address"].lower() in wanted
        ]
        return httpx.Response(200, json={"pairs": selected})

    respx.get(url__regex=TOKENS_URL).mock(side_effect=respond)
    async with dexscreener as client:
        rows, _ = await client.list_markets(chain="bsc", limit=50)

    requested = {address for batch in batches for address in batch}
    assert requested == set(SEED_TOKENS["bsc"])
    assert rows
    assert {row.chain for row in rows} == {"bsc"}


@respx.mock
async def test_all_chain_solana_rows_survive_empty_hint_feeds(
    dexscreener: DexScreenerClient,
) -> None:
    # Promotional feeds are optional. The canonical Solana seeds alone must produce rows.
    for path in (
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1",
        "/community-takeovers/latest/v1",
    ):
        respx.get(f"{BASE}{path}").mock(return_value=httpx.Response(200, json=[]))

    by_address = {
        address.lower(): {
            "chainId": "solana",
            "dexId": "raydium",
            "pairAddress": f"sol-pair-{index}",
            "baseToken": {"address": address, "symbol": symbol, "name": symbol},
            "quoteToken": {
                "address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                "symbol": "USDC",
                "name": "USD Coin",
            },
            "priceUsd": "1.5",
            "liquidity": {"usd": 1_000_000},
            "volume": {"h24": 500_000},
            "priceChange": {"h24": 2.5},
            "pairCreatedAt": 1_700_000_000_000,
        }
        for index, (address, symbol) in enumerate(
            zip(SEED_TOKENS["solana"], ("SOL", "USDC", "BONK", "JUP"), strict=True)
        )
    }
    requested: list[str] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        addresses = _addresses(request)
        requested.extend(addresses)
        selected = [by_address[address.lower()] for address in addresses if address.lower() in by_address]
        return httpx.Response(200, json={"pairs": selected})

    respx.get(url__regex=TOKENS_URL).mock(side_effect=respond)

    async with dexscreener as client:
        rows, _source, coverage = await client.discover_markets(chain="all", limit=1000)

    requested_lower = {address.lower() for address in requested}
    assert {address.lower() for address in SEED_TOKENS["solana"]} <= requested_lower
    solana_rows = [row for row in rows if row.chain == "solana"]
    assert {row.symbol for row in solana_rows} == {"SOL", "USDC", "BONK", "JUP"}
    assert next(item for item in coverage or [] if item.chain == "solana").reason == "pairs"


@respx.mock
async def test_change_h24_sort_keeps_unknown_last(dexscreener: DexScreenerClient) -> None:
    pairs = [
        make_change_pair("0xa", "A", h24="10"),
        make_change_pair("0xb", "B", h24="-5"),
        make_change_pair("0xc", "C"),
        make_change_pair("0xd", "D", h24="5"),
    ]
    respx.get(f"{BASE}/latest/dex/search").mock(
        return_value=httpx.Response(200, json={"pairs": pairs})
    )

    async with dexscreener as client:
        rows, _ = await client.list_markets(query="TEST", chain="all", sort="change_h24", limit=10)

    assert [row.symbol for row in rows] == ["A", "D", "B", "C"]
    assert rows[-1].price_change_h24 is None


@respx.mock
async def test_pair_age_sort_is_oldest_first_and_unknown_last(
    dexscreener: DexScreenerClient,
) -> None:
    pairs = [
        make_change_pair("0xa", "A", created_at=1_700_000_000_000),
        make_change_pair("0xb", "B", created_at=1_600_000_000_000),
        make_change_pair("0xc", "C", created_at=None),
        make_change_pair("0xd", "D", created_at=1_650_000_000_000),
    ]
    respx.get(f"{BASE}/latest/dex/search").mock(
        return_value=httpx.Response(200, json={"pairs": pairs})
    )

    async with dexscreener as client:
        rows, _ = await client.list_markets(query="TEST", chain="all", sort="pair_age", limit=10)

    assert [row.symbol for row in rows] == ["B", "D", "A", "C"]
    assert rows[-1].pair_created_at is None


@respx.mock
async def test_new_sort_keeps_unknown_pair_age_last(dexscreener: DexScreenerClient) -> None:
    pairs = [
        make_change_pair("0xa", "A", created_at=1_600_000_000_000),
        make_change_pair("0xb", "B", created_at=None),
        make_change_pair("0xc", "C", created_at=1_700_000_000_000),
    ]
    respx.get(f"{BASE}/latest/dex/search").mock(
        return_value=httpx.Response(200, json={"pairs": pairs})
    )

    async with dexscreener as client:
        rows, _ = await client.list_markets(query="TEST", chain="all", sort="new", limit=10)

    assert [row.symbol for row in rows] == ["C", "A", "B"]
    assert rows[-1].pair_created_at is None


def make_change_pair(
    address: str,
    symbol: str,
    *,
    h24: str | None = None,
    created_at: int | None = 1_700_000_000_000,
) -> dict[str, Any]:
    pair = _valid_pair()
    pair["pairAddress"] = f"pair-{address}"
    pair["baseToken"] = {"address": address, "symbol": symbol, "name": symbol}
    pair["priceChange"] = {} if h24 is None else {"h24": h24}
    if created_at is None:
        pair.pop("pairCreatedAt")
    else:
        pair["pairCreatedAt"] = created_at
    return pair


@respx.mock
async def test_429_sets_a_cooldown_and_fails_fast(
    dexscreener: DexScreenerClient, clock: Any
) -> None:
    route = respx.get(f"{BASE}/latest/dex/search").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "30"}),
            httpx.Response(200, json={"pairs": []}),
        ]
    )
    async with dexscreener as client:
        with pytest.raises(RateLimited):
            await client.search("WETH")
        with pytest.raises(RateLimited):
            await client.search("WETH")
        assert route.call_count == 1
        clock.advance(30.5)
        assert await client.search("WETH") == []
        assert route.call_count == 2


@respx.mock
async def test_5xx_is_an_upstream_error(dexscreener: DexScreenerClient) -> None:
    respx.get(f"{BASE}/latest/dex/search").mock(return_value=httpx.Response(503))
    async with dexscreener as client:
        with pytest.raises(UpstreamError):
            await client.search("WETH")


@respx.mock
async def test_network_failure_is_an_upstream_error(dexscreener: DexScreenerClient) -> None:
    respx.get(f"{BASE}/latest/dex/search").mock(side_effect=httpx.ConnectError("offline"))
    async with dexscreener as client:
        with pytest.raises(UpstreamError):
            await client.search("WETH")


@respx.mock
async def test_malformed_json_is_an_upstream_error(dexscreener: DexScreenerClient) -> None:
    respx.get(f"{BASE}/latest/dex/search").mock(
        return_value=httpx.Response(200, content=b"<html>not json</html>")
    )
    async with dexscreener as client:
        with pytest.raises(UpstreamError):
            await client.search("WETH")


@respx.mock
async def test_malformed_pairs_payload_is_an_upstream_error(
    dexscreener: DexScreenerClient,
) -> None:
    respx.get(f"{BASE}/latest/dex/search").mock(
        return_value=httpx.Response(200, json={"pairs": "nope"})
    )
    async with dexscreener as client:
        with pytest.raises(UpstreamError):
            await client.search("WETH")


def test_snapshot_resolves_the_requested_quote_side() -> None:
    pair = _valid_pair()
    pair["priceNative"] = "2000.5"
    meta = get_chain("ethereum")
    assert meta is not None
    snapshot = snapshot_from_pairs(meta, "0xquote", [pair])
    assert snapshot.symbol == "USDC"
    assert snapshot.name == "USD Coin"
    assert snapshot.price_usd == Decimal(1)
    assert snapshot.address == "0xquote"


def test_snapshot_fails_closed_when_quote_side_has_no_native_price() -> None:
    pair = _valid_pair()
    meta = get_chain("ethereum")
    assert meta is not None
    with pytest.raises(NotFound):
        snapshot_from_pairs(meta, "0xquote", [pair])
