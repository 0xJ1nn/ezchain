"""Data registry for chain-native transaction support.

The shared pipeline reads capabilities; it never branches on a chain name. This table is
the one place that maps a registry chain id to its decoder, builder and broadcast spec.
A chain id absent from the table is unsupported, never silently treated as a generic chain.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ezchain_core import TransactionAdapter
from ezchain_core.trust import DecodedFields

from ..registry import BY_ID
from .broadcast import BroadcastSpec, cosmos_spec, evm_spec, solana_spec, ton_spec
from .cosmos import CosmosBuilder, decode_cosmos_transaction
from .evm import EvmBuilder, decode_evm_transaction
from .solana import SolanaBuilder, decode_solana_transaction
from .sui import SuiBuilder, decode_sui_transaction
from .ton import TonBuilder, decode_ton_transaction

Decode = Callable[[bytes], DecodedFields]


@dataclass(frozen=True)
class ChainTxSupport:
    """One chain family's decode/build/broadcast capabilities."""

    chain: str
    family: str
    builder: Callable[[], TransactionAdapter]
    decoder: Decode
    broadcast: Callable[[], BroadcastSpec] | None


_EVM_CHAINS = (
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
)


def _support(
    chain: str,
    family: str,
    builder: Callable[[], TransactionAdapter],
    decoder: Decode,
    broadcast: Callable[[], BroadcastSpec] | None,
) -> ChainTxSupport:
    return ChainTxSupport(chain, family, builder, decoder, broadcast)


def _evm_builder(chain: str) -> Callable[[], TransactionAdapter]:
    """Bind each EVM chain to its real EIP-155 id, read from the chain registry."""
    meta = BY_ID.get(chain)
    if meta is None or meta.evm_chain_id is None:
        raise ValueError(f"{chain} has no registered EIP-155 chain id")
    chain_id = meta.evm_chain_id

    def factory() -> TransactionAdapter:
        return EvmBuilder(chain_id)

    return factory


SUPPORTED: dict[str, ChainTxSupport] = {
    "solana": _support("solana", "svm", SolanaBuilder, decode_solana_transaction, solana_spec),
    **{
        chain: _support(chain, "evm", _evm_builder(chain), decode_evm_transaction, evm_spec)
        for chain in _EVM_CHAINS
    },
    "ton": _support("ton", "tvm", TonBuilder, decode_ton_transaction, ton_spec),
    # Sui keeps its decoder and builder, but has no reviewed signed-broadcast request shape.
    "sui": _support("sui", "move", SuiBuilder, decode_sui_transaction, None),
    "osmosis": _support("osmosis", "cosmos", CosmosBuilder, decode_cosmos_transaction, cosmos_spec),
    "cosmos": _support("cosmos", "cosmos", CosmosBuilder, decode_cosmos_transaction, cosmos_spec),
}


def support_for(chain: str) -> ChainTxSupport | None:
    """The chain's capabilities, or ``None`` when no real adapter exists for it."""
    return SUPPORTED.get(chain)


__all__ = ["SUPPORTED", "ChainTxSupport", "support_for"]
