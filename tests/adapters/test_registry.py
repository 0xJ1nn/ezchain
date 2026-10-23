"""Registry contract: every chain has a ladder, and the ladder never lies.

A chain with no keyless executable source keeps ``paper_mark`` as its whole ladder. A
chain with an executable ladder must name a pricing stable and must never list
``paper_mark`` after a provider: a failed executable quote has to fail closed, not degrade
into a mark.
"""

from __future__ import annotations

import pytest

from ezchain_adapters import CHAINS, PAPER_MARK, PROVIDERS, get_chain


def test_every_chain_has_a_non_empty_ladder() -> None:
    for chain in CHAINS:
        assert chain.quote_providers, chain.id


def test_ladders_only_name_known_providers() -> None:
    known = set(PROVIDERS) | {PAPER_MARK}
    for chain in CHAINS:
        assert set(chain.quote_providers) <= known, chain.id


def test_paper_mark_is_only_ever_a_whole_ladder() -> None:
    for chain in CHAINS:
        if PAPER_MARK in chain.quote_providers:
            assert chain.quote_providers == (PAPER_MARK,), chain.id


def test_executable_chains_name_a_stable_and_mark_chains_do_not_need_one() -> None:
    for chain in CHAINS:
        if chain.quote_kind == PAPER_MARK:
            assert chain.executable_quotes is False
            assert chain.quote_asset is None or chain.quote_asset.decimals > 0
        else:
            assert chain.quote_asset is not None, chain.id
            assert chain.quote_asset.decimals >= 0, chain.id
            assert chain.executable_quotes is True


def test_quote_kind_is_the_head_of_the_ladder() -> None:
    for chain in CHAINS:
        assert chain.quote_kind == chain.quote_providers[0], chain.id


def test_evm_chains_carry_a_chain_id_for_the_provider_apis() -> None:
    for chain in CHAINS:
        if chain.family == "evm":
            assert chain.evm_chain_id is not None, chain.id


def test_verified_coverage_is_reported_truthfully() -> None:
    expected = {
        "solana": "jupiter",
        "sui": "cetus",
        "ton": "stonfi",
        "osmosis": "osmosis_sqs",
        "base": "kyberswap",
        "ethereum": "kyberswap",
        "linea": "kyberswap",
        "mantle": "lifi",
        "scroll": "lifi",
        "zksync": "lifi",
        "cronos": "lifi",
        "celo": "lifi",
        "metis": "lifi",
        "blast": PAPER_MARK,
        "fantom": PAPER_MARK,
        "moonbeam": PAPER_MARK,
        "aptos": PAPER_MARK,
        "tron": PAPER_MARK,
        "near": PAPER_MARK,
        "cosmos": PAPER_MARK,
    }
    for chain_id, kind in expected.items():
        meta = get_chain(chain_id)
        assert meta is not None
        assert meta.quote_kind == kind, chain_id


def test_chain_capabilities_serialize_the_ladder() -> None:
    base = get_chain("base")
    assert base is not None
    payload = base.to_dict()
    capabilities = payload["capabilities"]
    assert capabilities["quote_kind"] == "kyberswap"
    assert capabilities["quote_providers"] == ["kyberswap", "velora", "lifi"]
    assert capabilities["executable_quotes"] is True
    assert capabilities["live_execute"] is False


def test_unknown_ladder_provider_is_rejected_at_import() -> None:
    from ezchain_adapters.registry import ChainMeta

    with pytest.raises(ValueError):
        ChainMeta("test", "Test", "evm", "test", "TST", quote_providers=("nope",))


def test_mark_after_a_provider_is_rejected_at_import() -> None:
    from ezchain_adapters.registry import ChainMeta

    with pytest.raises(ValueError):
        ChainMeta(
            "test",
            "Test",
            "evm",
            "test",
            "TST",
            quote_providers=("kyberswap", PAPER_MARK),
        )
