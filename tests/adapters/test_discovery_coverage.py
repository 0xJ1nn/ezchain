"""All-chain discovery coverage.

The public `/latest/dex/tokens` endpoint caps a response at 30 *pairs*, so a global
address batch cannot cover 25 chains. These tests model that cap, unregistered-chain
noise and the per-chain budget. All HTTP is mocked with respx.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx
import pytest
import respx

from ezchain_adapters import ChainCoverage, DexScreenerClient
from ezchain_adapters.dexscreener import BASE, MAX_BATCH, MAX_CONCURRENT_CHAINS
from ezchain_adapters.registry import CHAINS
from ezchain_adapters.seeds import SEED_TOKENS

NON_DATA_CHAINS = {"osmosis", "cosmos", "moonbeam"}
DATA_CHAINS = [chain.id for chain in CHAINS if chain.id not in NON_DATA_CHAINS]
NEW_EVM_CHAINS = ("blast", "mantle", "linea", "scroll", "zksync")
SOLANA_HINT = "So11111111111111111111111111111111111111112"
SHARED_WETH = "0x4200000000000000000000000000000000000006"
TOKENS_URL = rf"^{re.escape(BASE)}/latest/dex/tokens/.+$"
TOKEN_PAIRS_URL = rf"^{re.escape(BASE)}/token-pairs/v1/.+$"


def _addresses(request: httpx.Request) -> list[str]:
    return request.url.path.split("/latest/dex/tokens/", 1)[1].split(",")


def make_pair(
    chain: str,
    base_address: str,
    *,
    symbol: str = "TEST",
    liquidity: str = "100000",
    volume: str = "10000",
    pair_address: str | None = None,
    chain_id: str | None = None,
) -> dict[str, Any]:
    return {
        "chainId": chain_id or chain,
        "dexId": "testdex",
        "pairAddress": pair_address or f"pair-{chain}-{base_address[-6:]}",
        "baseToken": {"address": base_address, "symbol": symbol, "name": symbol},
        "quoteToken": {"address": "0xquote", "symbol": "USDC", "name": "USD Coin"},
        "priceUsd": "1.5",
        "liquidity": {"usd": liquidity},
        "volume": {"h24": volume},
        "priceChange": {"m5": 1, "h1": 2},
        "pairCreatedAt": 1_700_000_000_000,
        "url": f"https://dexscreener.com/{chain}/{pair_address or 'pair'}",
    }


def _seed_address(chain: str) -> str:
    if chain == "solana":
        return SOLANA_HINT
    return SEED_TOKENS[chain][0]


PAIRS_PER_TOKEN = 10


def data_pairs(*, noise: int = 0) -> dict[str, list[dict[str, Any]]]:
    """Ten registered pairs per data chain, plus unregistered WETH noise.

    Ten pairs per token is the point: one global 30-pair response can only cover three
    chains, so the old global-batch algorithm cannot reach late-registry chains.
    """
    by_address: dict[str, list[dict[str, Any]]] = {}
    for chain in DATA_CHAINS:
        address = _seed_address(chain)
        by_address.setdefault(address.lower(), []).extend(
            make_pair(
                chain,
                address,
                pair_address=f"pair-{chain}-{index}",
                liquidity=str(1_000_000 - index * 1000),
            )
            for index in range(PAIRS_PER_TOKEN)
        )
    if noise:
        for index in range(noise):
            unregistered = ("pulsechain", "ink", "soneium", "worldchain")[index % 4]
            noise_pair = make_pair(
                unregistered,
                SHARED_WETH,
                pair_address=f"noise-{index}",
                chain_id=unregistered,
            )
            by_address.setdefault(SHARED_WETH.lower(), []).append(noise_pair)
    return by_address


def mock_hint_feeds(*, solana_hint: str = SOLANA_HINT) -> None:
    profiles = [{"chainId": "solana", "tokenAddress": solana_hint}] if solana_hint else []
    respx.get(f"{BASE}/token-profiles/latest/v1").mock(
        return_value=httpx.Response(200, json=profiles)
    )
    for path in ("/token-boosts/latest/v1", "/token-boosts/top/v1"):
        respx.get(f"{BASE}{path}").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{BASE}/community-takeovers/latest/v1").mock(
        return_value=httpx.Response(200, json=[])
    )


def mock_tokens(
    by_address: dict[str, list[dict[str, Any]]],
    *,
    on_request: Any = None,
    fallback: dict[str, list[dict[str, Any]]] | None = None,
) -> list[list[str]]:
    batches: list[list[str]] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        addresses = _addresses(request)
        batches.append(addresses)
        if on_request is not None:
            await on_request()
        selected: dict[str, dict[str, Any]] = {}
        for address in addresses:
            for pair in by_address.get(address.lower(), []):
                selected[pair["pairAddress"]] = pair
        # The upstream response is capped at 30 pairs, independent of address count.
        return httpx.Response(200, json={"pairs": list(selected.values())[:30]})

    async def fallback_respond(request: httpx.Request) -> httpx.Response:
        chain = request.url.path.split("/token-pairs/v1/", 1)[1].split("/", 1)[0]
        return httpx.Response(200, json=(fallback or {}).get(chain, []))

    respx.get(url__regex=TOKENS_URL).mock(side_effect=respond)
    respx.get(url__regex=TOKEN_PAIRS_URL).mock(side_effect=fallback_respond)
    return batches


@respx.mock
async def test_all_chain_scan_covers_every_seeded_chain(
    dexscreener: DexScreenerClient,
) -> None:
    mock_hint_feeds()
    batches = mock_tokens(data_pairs())

    async with dexscreener as client:
        rows, source, coverage = await client.discover_markets(chain="all", limit=100)

    assert coverage is not None
    covered = {row.chain for row in rows}
    assert covered == set(DATA_CHAINS)
    assert {item.chain for item in coverage} == {chain.id for chain in CHAINS}
    assert all(item.reason == "pairs" for item in coverage if item.chain in DATA_CHAINS)
    assert all(item.reason == "no_pairs" for item in coverage if item.chain in NON_DATA_CHAINS)
    assert len(batches) <= len(CHAINS)
    assert all(len(batch) <= MAX_BATCH for batch in batches)
    assert all(row.source.observed_at == source.observed_at for row in rows)


@respx.mock
async def test_failed_chain_probe_reports_error_not_no_pairs(
    dexscreener: DexScreenerClient,
) -> None:
    mock_hint_feeds()
    eth_seed = SEED_TOKENS["ethereum"][0].lower()

    async def respond(request: httpx.Request) -> httpx.Response:
        addresses = {address.lower() for address in _addresses(request)}
        if eth_seed in addresses:
            return httpx.Response(503)
        return httpx.Response(200, json={"pairs": []})

    respx.get(url__regex=TOKENS_URL).mock(side_effect=respond)
    respx.get(url__regex=TOKEN_PAIRS_URL).mock(return_value=httpx.Response(200, json=[]))

    async with dexscreener as client:
        _rows, _source, coverage = await client.discover_markets(chain="all", limit=100)

    by_chain = {item.chain: item for item in coverage or []}
    assert by_chain["ethereum"].reason == "error"
    assert by_chain["ethereum"].returned == 0
    assert by_chain["solana"].reason == "no_pairs"


@respx.mock
async def test_late_registry_chain_survives_the_30_pair_response_cap(
    dexscreener: DexScreenerClient,
) -> None:
    # A single global response would hold 30 pairs: at 10 pairs per token that is at
    # most three chains, so zksync (near the end of the registry) could never appear.
    assert MAX_BATCH // PAIRS_PER_TOKEN < DATA_CHAINS.index("zksync")
    mock_hint_feeds()
    mock_tokens(data_pairs())

    async with dexscreener as client:
        rows, _source, coverage = await client.discover_markets(chain="all", limit=100)

    assert "zksync" in {row.chain for row in rows}
    by_chain = {item.chain: item for item in coverage or []}
    assert by_chain["zksync"].returned >= 1


@respx.mock
async def test_unregistered_weth_noise_does_not_consume_registered_budget(
    dexscreener: DexScreenerClient,
) -> None:
    mock_hint_feeds()
    # The shared Base/Optimism WETH response is full: 28 noise pairs plus the two
    # registered pairs. Per-chain fan-out still returns rows for both chains.
    mock_tokens(data_pairs(noise=28))

    async with dexscreener as client:
        rows, _source, coverage = await client.discover_markets(chain="all", limit=100)

    chains = {row.chain for row in rows}
    assert {"base", "optimism"} <= chains
    assert not chains & {"pulsechain", "ink", "soneium", "worldchain"}
    by_chain = {item.chain: item for item in coverage or []}
    assert by_chain["base"].returned >= 1
    assert by_chain["optimism"].returned >= 1


@respx.mock
async def test_shared_weth_cap_falls_back_to_chain_filtered_lookup(
    dexscreener: DexScreenerClient,
) -> None:
    mock_hint_feeds()
    noise = [
        make_pair(
            ("pulsechain", "ink", "soneium", "worldchain")[index % 4],
            SHARED_WETH,
            pair_address=f"noise-{index}",
            chain_id=("pulsechain", "ink", "soneium", "worldchain")[index % 4],
        )
        for index in range(30)
    ]
    fallback = {
        "base": [make_pair("base", _seed_address("base"), pair_address="base-fallback")],
        "optimism": [make_pair("optimism", _seed_address("optimism"), pair_address="op-fallback")],
    }
    mock_tokens({SHARED_WETH.lower(): noise}, fallback=fallback)

    async with dexscreener as client:
        rows, _source, coverage = await client.discover_markets(chain="all", limit=100)

    by_chain = {item.chain: item for item in coverage or []}
    assert by_chain["base"].reason == "pairs"
    assert by_chain["optimism"].reason == "pairs"
    assert {"base", "optimism"} <= {row.chain for row in rows}
    assert not {row.chain for row in rows} & {"pulsechain", "ink", "soneium", "worldchain"}


@pytest.mark.parametrize("chain", DATA_CHAINS)
@respx.mock
async def test_every_data_chain_is_represented_or_reported_no_pairs(
    chain: str, dexscreener: DexScreenerClient
) -> None:
    mock_hint_feeds()
    mock_tokens(data_pairs())

    async with dexscreener as client:
        rows, _source, coverage = await client.discover_markets(chain="all", limit=100)

    by_chain = {item.chain: item for item in coverage or []}
    assert by_chain[chain].reason == "pairs"
    assert by_chain[chain].returned >= 1
    assert any(row.chain == chain for row in rows)


@respx.mock
async def test_new_evm_chains_are_requested_in_all_chain_scan(
    dexscreener: DexScreenerClient,
) -> None:
    mock_hint_feeds()
    batches = mock_tokens(data_pairs())

    async with dexscreener as client:
        await client.discover_markets(chain="all", limit=100)

    requested = {address.lower() for batch in batches for address in batch}
    for chain in NEW_EVM_CHAINS:
        assert any(address.lower() in requested for address in SEED_TOKENS[chain])


@respx.mock
async def test_all_chain_token_lookups_stay_within_one_per_chain(
    dexscreener: DexScreenerClient,
) -> None:
    mock_hint_feeds()
    active = 0
    peak = 0

    async def track() -> None:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1

    token_batches = mock_tokens({}, on_request=track)

    async with dexscreener as client:
        await client.discover_markets(chain="all", limit=100)

    # At most one token request per registered chain; the four hint feeds are extra.
    assert len(token_batches) <= len(CHAINS)
    assert 1 <= peak <= MAX_CONCURRENT_CHAINS


@pytest.mark.parametrize("sort", ["trending", "liquidity", "new"])
@respx.mock
async def test_sort_modes_are_none_safe_and_keep_unknowns_null(
    sort: str, dexscreener: DexScreenerClient
) -> None:
    pairs = [
        make_pair("ethereum", "0xa", pair_address="a", liquidity="1000", volume="900"),
        make_pair("ethereum", "0xb", pair_address="b", liquidity="50"),
        make_pair("ethereum", "0xc", pair_address="c", volume="100"),
        make_pair("ethereum", "0xd", pair_address="d"),
    ]
    pairs[1].pop("volume")
    pairs[2].pop("liquidity")
    pairs[3].pop("volume")
    pairs[3].pop("liquidity")
    respx.get(f"{BASE}/latest/dex/search").mock(
        return_value=httpx.Response(200, json={"pairs": pairs})
    )

    async with dexscreener as client:
        rows, _source, coverage = await client.discover_markets(
            query="TEST", chain="ethereum", sort=sort, limit=10
        )

    assert coverage is None
    by_address = {row.pair_address: row for row in rows}
    assert by_address["b"].volume_24h_usd is None
    assert by_address["c"].liquidity_usd is None
    assert by_address["d"].volume_24h_usd is None
    assert by_address["d"].liquidity_usd is None
    if sort == "trending":
        # Known volume first; missing volume never sorts as zero ahead of a known row.
        assert [row.pair_address for row in rows] == ["a", "c", "b", "d"]
    elif sort == "liquidity":
        assert [row.pair_address for row in rows] == ["a", "b", "c", "d"]


@respx.mock
async def test_search_reports_no_coverage_and_sorts_globally(
    dexscreener: DexScreenerClient,
) -> None:
    pairs = [
        make_pair("ethereum", "0xaaa", volume="10"),
        make_pair("ethereum", "0xbbb", volume="900"),
    ]
    respx.get(f"{BASE}/latest/dex/search").mock(
        return_value=httpx.Response(200, json={"pairs": pairs})
    )

    async with dexscreener as client:
        rows, _source, coverage = await client.discover_markets(
            query="TEST", chain="all", sort="trending", limit=50
        )

    assert coverage is None
    assert [row.base_token for row in rows] == ["0xbbb", "0xaaa"]


@respx.mock
async def test_round_robin_keeps_low_liquidity_chains_before_the_limit(
    dexscreener: DexScreenerClient,
) -> None:
    rows_by_chain = {
        "ethereum": [
            make_pair("ethereum", _seed_address("ethereum"), pair_address="eth1"),
            make_pair("ethereum", "0xeth2", pair_address="eth2"),
        ],
        "base": [
            make_pair("base", _seed_address("base"), pair_address="base1"),
            make_pair("base", "0xbase2", pair_address="base2"),
        ],
        "solana": [
            make_pair("solana", _seed_address("solana"), pair_address="sol1"),
            make_pair("solana", "0xsol2", pair_address="sol2"),
        ],
    }
    respx.get(f"{BASE}/token-profiles/latest/v1").mock(
        return_value=httpx.Response(200, json=[{"chainId": "solana", "tokenAddress": SOLANA_HINT}])
    )
    for path in ("/token-boosts/latest/v1", "/token-boosts/top/v1"):
        respx.get(f"{BASE}{path}").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{BASE}/community-takeovers/latest/v1").mock(
        return_value=httpx.Response(200, json=[])
    )

    async def respond(request: httpx.Request) -> httpx.Response:
        selected = []
        for address in _addresses(request):
            for pairs in rows_by_chain.values():
                for pair in pairs:
                    if pair["baseToken"]["address"].lower() == address.lower():
                        selected.append(pair)
        return httpx.Response(200, json={"pairs": selected})

    respx.get(url__regex=TOKENS_URL).mock(side_effect=respond)
    async with dexscreener as client:
        rows, _source, coverage = await client.discover_markets(
            chain="all", sort="liquidity", limit=3
        )

    # Round-robin across registry order takes one row per chain before the limit.
    assert {row.chain for row in rows} == {"solana", "ethereum", "base"}
    assert coverage is not None


def test_coverage_serializes_without_money_fields() -> None:
    coverage = ChainCoverage("blast", 2, 1, "pairs")
    assert coverage.to_dict() == {
        "chain": "blast",
        "requested": 2,
        "returned": 1,
        "reason": "pairs",
    }
