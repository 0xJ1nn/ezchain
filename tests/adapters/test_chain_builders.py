"""Builders sit behind the shared transaction-adapter protocol, and selection is data."""

from __future__ import annotations

from decimal import Decimal

import pytest

from ezchain_adapters.chains.registry import SUPPORTED, support_for
from ezchain_adapters.registry import CHAINS
from ezchain_core import ApprovedIntent, TransactionAdapter
from ezchain_core.trust import BINDING_FIELDS, Intent, decode_and_bind

EVM_CHAINS = {
    "ethereum",
    "base",
    "arbitrum",
    "polygon",
    "bsc",
    "optimism",
    "avalanche",
    "blast",
    "mantle",
    "linea",
    "scroll",
    "zksync",
    "fantom",
    "cronos",
    "celo",
    "moonbeam",
    "metis",
}


def approved(chain: str) -> ApprovedIntent:
    return ApprovedIntent(
        chain=chain,
        cluster="testnet",
        mint="mint-test",
        side="buy",
        amount=Decimal("100"),
        min_out=Decimal("50"),
        recipient="recipient-test",
        program_id="program-test",
        idempotency_key="idem-chain-builders-0001",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("chain", sorted(SUPPORTED))
async def test_every_supported_chain_builds_bytes_that_bind(chain: str) -> None:
    support = support_for(chain)
    assert support is not None
    builder = support.builder()
    assert isinstance(builder, TransactionAdapter)
    raw = await builder.prepare(approved(chain))
    fields = support.decoder(raw)
    trust_intent = Intent.from_mapping(
        {
            "chain": approved(chain).chain,
            "cluster": approved(chain).cluster,
            "mint": approved(chain).mint,
            "side": approved(chain).side,
            "amount": approved(chain).amount,
            "minimum_output": approved(chain).min_out,
            "recipient": approved(chain).recipient,
            "program_id": approved(chain).program_id,
            "idempotency_key": approved(chain).idempotency_key,
        }
    )
    assert decode_and_bind(trust_intent, fields=fields)
    assert set(BINDING_FIELDS) == {
        "chain",
        "cluster",
        "mint",
        "side",
        "amount",
        "minimum_output",
        "recipient",
        "program_id",
        "idempotency_key",
    }


def test_registry_covers_exactly_the_five_implemented_families() -> None:
    families = {support.family for support in SUPPORTED.values()}
    assert families == {"svm", "evm", "tvm", "move", "cosmos"}
    assert set(SUPPORTED) == {"solana", "ton", "sui", "osmosis", "cosmos"} | EVM_CHAINS
    assert {chain.id for chain in CHAINS} >= set(SUPPORTED)


@pytest.mark.parametrize("chain", ["aptos", "tron", "near", "unknown-chain"])
def test_unimplemented_chains_fail_closed(chain: str) -> None:
    assert support_for(chain) is None
