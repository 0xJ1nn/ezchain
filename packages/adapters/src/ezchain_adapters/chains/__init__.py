"""Chain-native unsigned-transaction decoders, builders and broadcast adapters.

One module per chain family. The shared pipeline never branches on a chain name: it reads
the registry in :mod:`ezchain_adapters.chains.registry`, which maps a chain id to a builder,
a decoder and a broadcast specification. Signing stays behind ``SignerTransport`` in the
executor; nothing here holds a key or signs. A broadcast receipt is ``submitted``, never a
``Fill``: executor ``Venue.submit`` wiring stays undone until every chain has a reviewed
confirmation/status path and reconciliation.
"""

from __future__ import annotations

from .broadcast import (
    BroadcastError,
    BroadcastReceipt,
    BroadcastSpec,
    ChainBroadcaster,
    SignedBroadcastSpec,
    sui_spec,
    unsupported_broadcast,
)
from .chainbind import ChainBindingDecodeError, decode_binding_payload, encode_binding_payload
from .registry import SUPPORTED, ChainTxSupport, support_for
from .status import (
    CONFIRMATION_ACTIONS,
    CONFIRMATION_STATES,
    ChainStatusReader,
    Confirmation,
    cosmos_status_spec,
    evm_status_spec,
    solana_status_spec,
    sui_status_spec,
    ton_status_spec,
)

__all__ = [
    "SUPPORTED",
    "BroadcastError",
    "BroadcastReceipt",
    "BroadcastSpec",
    "ChainBindingDecodeError",
    "ChainBroadcaster",
    "CONFIRMATION_ACTIONS",
    "CONFIRMATION_STATES",
    "ChainStatusReader",
    "ChainTxSupport",
    "Confirmation",
    "SignedBroadcastSpec",
    "cosmos_status_spec",
    "decode_binding_payload",
    "normalize_confirmation",
    "encode_binding_payload",
    "evm_status_spec",
    "solana_status_spec",
    "sui_spec",
    "sui_status_spec",
    "support_for",
    "ton_status_spec",
    "unsupported_broadcast",
]
