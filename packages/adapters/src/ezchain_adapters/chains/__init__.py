"""Chain-native unsigned-transaction decoders, builders and broadcast adapters.

One module per chain family. The shared pipeline never branches on a chain name: it reads
the registry in :mod:`ezchain_adapters.chains.registry`, which maps a chain id to a builder,
a decoder and a broadcast specification. Signing stays behind ``SignerTransport`` in the
executor; nothing here holds a key or signs. A broadcast receipt is ``submitted``, never a
``Fill``: executor ``Venue.submit`` wiring stays undone until every chain has a reviewed
confirmation/status path and reconciliation.
"""

from __future__ import annotations

from .broadcast import BroadcastError, BroadcastReceipt, BroadcastSpec, ChainBroadcaster
from .chainbind import ChainBindingDecodeError, decode_binding_payload, encode_binding_payload
from .registry import SUPPORTED, ChainTxSupport, support_for

__all__ = [
    "SUPPORTED",
    "BroadcastError",
    "BroadcastReceipt",
    "BroadcastSpec",
    "ChainBindingDecodeError",
    "ChainBroadcaster",
    "ChainTxSupport",
    "decode_binding_payload",
    "encode_binding_payload",
    "support_for",
]
