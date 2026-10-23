"""Seed-token coverage: every advertised chain can contribute discovery rows."""

from __future__ import annotations

import pytest

from ezchain_adapters.dexscreener import MAX_DISCOVERY_ADDRESSES
from ezchain_adapters.registry import BY_ID
from ezchain_adapters.seeds import SEED_TOKENS, seed_addresses

REQUIRED_CHAINS = {
    "solana",
    "ethereum",
    "base",
    "arbitrum",
    "polygon",
    "optimism",
    "bsc",
    "avalanche",
    "sui",
    "aptos",
    "ton",
    "tron",
    "near",
    "blast",
    "mantle",
    "linea",
    "scroll",
    "zksync",
    "osmosis",
    "cosmos",
    "fantom",
    "cronos",
    "celo",
    "moonbeam",
    "metis",
}


def test_seed_chains_exist_in_the_registry() -> None:
    assert set(SEED_TOKENS) <= set(BY_ID)


def test_every_required_chain_has_a_seed() -> None:
    assert REQUIRED_CHAINS <= set(SEED_TOKENS)


@pytest.mark.parametrize("chain", sorted(SEED_TOKENS))
def test_seed_addresses_are_clean_and_unique(chain: str) -> None:
    addresses = SEED_TOKENS[chain]
    assert addresses
    for address in addresses:
        assert address
        assert address == address.strip()
    assert len(addresses) == len({address.lower() for address in addresses})


def test_solana_seeds_are_the_canonical_majors() -> None:
    addresses = {address.lower() for address in SEED_TOKENS["solana"]}
    assert addresses == {
        "so11111111111111111111111111111111111111112",  # SOL
        "epjfwdd5aufqssqem2qn1xzybapc8g4weggkzwytdt1v",  # USDC
        "dezxaz8z7pnrnrjjz3wxborgixca6xjnb7yab1ppb263",  # BONK
        "jupyiwryjfskupiha7hker8vutaefosybkedznsdvcn",  # JUP
    }


def test_all_chains_dedupe_shared_addresses() -> None:
    addresses = seed_addresses("all")
    assert len(addresses) == len({address.lower() for address in addresses})
    shared_weth = "0x4200000000000000000000000000000000000006"
    assert [address.lower() for address in addresses].count(shared_weth.lower()) == 1


def test_all_chains_stay_within_the_discovery_budget() -> None:
    addresses = seed_addresses("all")
    assert 0 < len(addresses) <= MAX_DISCOVERY_ADDRESSES


@pytest.mark.parametrize("selector", ["all", None])
def test_all_selector_returns_every_chain(selector: str | None) -> None:
    assert seed_addresses(selector) == seed_addresses("all")


def test_single_chain_returns_only_that_chain() -> None:
    assert seed_addresses("bsc") == SEED_TOKENS["bsc"]
    assert seed_addresses("unknown-chain") == ()
