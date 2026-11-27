"""TON unsigned-message decoder and builder.

The wire form is a minimal, valid Bag-of-Cells container: the published ``b5ee9c72``
magic, a one-byte reference-index flag, one cell with no references, and that cell's two
descriptor bytes followed by its data. The binding payload is the whole cell body. This is
a documented subset of BoC, not a TON VM encoder; it never signs and holds no key.
"""

from __future__ import annotations

from dataclasses import replace

from ezchain_core import ApprovedIntent
from ezchain_core.trust import DecodedFields

from .chainbind import (
    BINDING_MAGIC,
    ChainBindingDecodeError,
    decode_binding_payload,
    encode_binding_payload,
    fields_from_approval,
)

_BOC_MAGIC = bytes.fromhex("b5ee9c72")
_FLAGS = 0x01  # one-byte reference index, no index array, no CRC, level 0
_CELL_DESCRIPTOR = bytes((0x00,))  # zero references, level 0


def build_ton_transaction(fields: DecodedFields) -> bytes:
    """Build a single-cell BoC whose body is the binding payload."""
    payload = encode_binding_payload(fields)
    # BoC's second descriptor byte pair is the cell's bit length (a whole number of bytes
    # here); two bytes cover the binding payload without a size ceiling.
    descriptor = bytes((0x00,)) + len(payload).to_bytes(2, "big")
    return b"".join(
        (
            _BOC_MAGIC,
            bytes((_FLAGS, 0, 1, 0)),  # refs, cells, incomplete
            descriptor,
            payload,
        )
    )


def decode_ton_transaction(raw: bytes) -> DecodedFields:
    """Decode a single-cell BoC into the trust boundary's field map."""
    if not isinstance(raw, bytes) or not raw.startswith(_BOC_MAGIC):
        raise ChainBindingDecodeError("the transaction is not a Bag-of-Cells container")
    header = 4 + 4 + 3
    if len(raw) < header:
        raise ChainBindingDecodeError("the BoC header is truncated")
    if raw[4] != _FLAGS or raw[5] != 0 or raw[6] != 1 or raw[7] != 0:
        raise ChainBindingDecodeError("the BoC topology is unsupported")
    if raw[8:9] != _CELL_DESCRIPTOR:
        raise ChainBindingDecodeError("the cell has references or a non-zero level")
    size = int.from_bytes(raw[9:11], "big")
    if len(raw) - header != size:
        raise ChainBindingDecodeError("the cell body length does not match its descriptor")
    data = raw[header:]
    if not data.startswith(BINDING_MAGIC):
        raise ChainBindingDecodeError("the transaction has no binding payload")
    return replace(
        decode_binding_payload(data),
        version="boc-v1",
        size_bytes=len(raw),
        instruction_count=1,
    )


class TonBuilder:
    """Builds unsigned single-cell BoC messages from an immutable approval only."""

    async def prepare(self, approved: ApprovedIntent) -> bytes:
        fields = fields_from_approval(approved, version="boc-v1")
        return build_ton_transaction(fields)


__all__ = ["TonBuilder", "build_ton_transaction", "decode_ton_transaction"]
