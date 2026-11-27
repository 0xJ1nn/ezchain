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

from .broadcast import BroadcastSpec, cosmos_spec, evm_spec, solana_spec, sui_spec, ton_spec
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
    builder: type[TransactionAdapter]
    decoder: Decode
    broadcast: Callable[[], BroadcastSpec]


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
    builder: type[TransactionAdapter],
    decoder: Decode,
    broadcast: Callable[[], BroadcastSpec],
) -> ChainTxSupport:
    return ChainTxSupport(chain, family, builder, decoder, broadcast)


SUPPORTED: dict[str, ChainTxSupport] = {
    "solana": _support("solana", "svm", SolanaBuilder, decode_solana_transaction, solana_spec),
    **{
        chain: _support(chain, "evm", EvmBuilder, decode_evm_transaction, evm_spec)
        for chain in _EVM_CHAINS
    },
    "ton": _support("ton", "tvm", TonBuilder, decode_ton_transaction, ton_spec),
    "sui": _support("sui", "move", SuiBuilder, decode_sui_transaction, sui_spec),
    "osmosis": _support("osmosis", "cosmos", CosmosBuilder, decode_cosmos_transaction, cosmos_spec),
    "cosmos": _support("cosmos", "cosmos", CosmosBuilder, decode_cosmos_transaction, cosmos_spec),
}


def support_for(chain: str) -> ChainTxSupport | None:
    """The chain's capabilities, or ``None`` when no real adapter exists for it."""
    return SUPPORTED.get(chain)


__all__ = ["SUPPORTED", "ChainTxSupport", "support_for"]
