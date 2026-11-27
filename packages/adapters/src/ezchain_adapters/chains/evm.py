"""EVM unsigned-transaction decoder and builder.

The wire form is the published RLP legacy transaction with EIP-155's ``v = chain_id`` and
empty ``r``/``s`` (an unsigned transaction). The binding payload is carried in the
transaction's data field. This module parses those bytes; it never signs and never holds a
key. The router address below is a documented non-secret test vector.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any, cast

from ezchain_core import ApprovedIntent
from ezchain_core.trust import DecodedFields

from .chainbind import (
    BINDING_MAGIC,
    ChainBindingDecodeError,
    decode_binding_payload,
    encode_binding_payload,
    fields_from_approval,
)

_ROUTER = bytes((0xAA,)) * 20
_CHAIN_ID = 1


def rlp_encode(item: bytes | Sequence[Any]) -> bytes:
    """Canonical RLP for one byte string or a list of items."""
    if isinstance(item, bytes):
        if len(item) == 1 and item[0] < 0x80:
            return item
        if len(item) <= 55:
            return bytes((0x80 + len(item),)) + item
        length = len(item).to_bytes((len(item).bit_length() + 7) // 8, "big")
        return bytes((0xB7 + len(length),)) + length + item
    parts = cast(Sequence[Any], item)
    payload = b"".join(rlp_encode(part) for part in parts)
    if len(payload) <= 55:
        return bytes((0xC0 + len(payload),)) + payload
    length = len(payload).to_bytes((len(payload).bit_length() + 7) // 8, "big")
    return bytes((0xF7 + len(length),)) + length + payload


def _read_length(raw: bytes, offset: int, count: int) -> tuple[int, int]:
    if count == 0:
        raise ChainBindingDecodeError("an RLP length has no bytes")
    if offset + count > len(raw):
        raise ChainBindingDecodeError("an RLP length is truncated")
    if count > 1 and raw[offset] == 0:
        raise ChainBindingDecodeError("an RLP length has a leading zero")
    size = int.from_bytes(raw[offset : offset + count], "big")
    if count == 1 and size <= 55:
        raise ChainBindingDecodeError("an RLP length is non-canonical")
    return size, offset + count


def rlp_decode(raw: bytes) -> bytes | list[object]:
    """Decode one canonical RLP item, refusing trailing bytes and non-canonical lengths."""
    if not raw:
        raise ChainBindingDecodeError("the RLP item is empty")
    item, offset = _decode_one(raw, 0)
    if offset != len(raw):
        raise ChainBindingDecodeError("the RLP item has trailing bytes")
    return item


def _decode_items(raw: bytes) -> list[object]:
    items: list[object] = []
    offset = 0
    while offset < len(raw):
        before = offset
        item, offset = _decode_one(raw, offset)
        if offset <= before:
            raise ChainBindingDecodeError("an RLP item did not advance")
        items.append(item)
    return items


def _decode_one(raw: bytes, offset: int) -> tuple[bytes | list[object], int]:
    """Decode the item at ``offset`` and report where it ended."""
    if offset >= len(raw):
        raise ChainBindingDecodeError("an RLP item is truncated")
    prefix = raw[offset]
    if prefix < 0x80:
        return raw[offset : offset + 1], offset + 1
    if prefix <= 0xB7:
        size = prefix - 0x80
        end = offset + 1 + size
        if end > len(raw):
            raise ChainBindingDecodeError("an RLP string is truncated")
        chunk = raw[offset + 1 : end]
        if size == 1 and chunk[0] < 0x80:
            raise ChainBindingDecodeError("an RLP string is non-canonical")
        return chunk, end
    if prefix <= 0xBF:
        count = prefix - 0xB7
        size, start = _read_length(raw, offset + 1, count)
        end = start + size
        if end > len(raw) or size <= 55:
            raise ChainBindingDecodeError("an RLP long string is malformed")
        return raw[start:end], end
    if prefix <= 0xF7:
        size = prefix - 0xC0
        end = offset + 1 + size
        if end > len(raw):
            raise ChainBindingDecodeError("an RLP list is truncated")
        return _decode_items(raw[offset + 1 : end]), end
    count = prefix - 0xF7
    size, start = _read_length(raw, offset + 1, count)
    end = start + size
    if end > len(raw) or size <= 55:
        raise ChainBindingDecodeError("an RLP long list is malformed")
    return _decode_items(raw[start:end]), end


def build_evm_transaction(fields: DecodedFields) -> bytes:
    """Build an unsigned EIP-155 legacy transaction carrying the binding payload."""
    payload = encode_binding_payload(fields)
    tx: list[Any] = [
        b"",  # nonce 0
        b"",  # gas price 0
        b"",  # gas limit 0
        _ROUTER,
        b"",  # value 0
        payload,
        bytes((_CHAIN_ID,)),  # v = chain id, r/s empty: this is not signed
        b"",
        b"",
    ]
    return rlp_encode(tx)


def decode_evm_transaction(raw: bytes) -> DecodedFields:
    """Decode an unsigned EVM transaction into the trust boundary's field map."""
    if not isinstance(raw, bytes) or not raw:
        raise ChainBindingDecodeError("the transaction is empty")
    item = rlp_decode(raw)
    if not isinstance(item, list) or len(item) != 9:
        raise ChainBindingDecodeError("the transaction is not a legacy RLP transaction")
    to, data = item[3], item[5]
    if not isinstance(to, bytes) or len(to) != 20 or not isinstance(data, bytes):
        raise ChainBindingDecodeError("the transaction has no router or data")
    if not data.startswith(BINDING_MAGIC):
        raise ChainBindingDecodeError("the transaction has no binding payload")
    return replace(
        decode_binding_payload(data),
        version="eip155-legacy",
        size_bytes=len(raw),
        instruction_count=1,
        allowance_spender="0x" + to.hex(),
        allowance_unlimited=False,
    )


class EvmBuilder:
    """Builds unsigned EVM transactions from an immutable approval only."""

    async def prepare(self, approved: ApprovedIntent) -> bytes:
        fields = fields_from_approval(
            approved,
            version="eip155-legacy",
            allowance_spender="0x" + _ROUTER.hex(),
            allowance_unlimited=False,
        )
        return build_evm_transaction(fields)


__all__ = [
    "EvmBuilder",
    "build_evm_transaction",
    "decode_evm_transaction",
    "rlp_decode",
    "rlp_encode",
]
