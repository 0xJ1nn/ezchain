"""Cosmos SDK unsigned-transaction decoder and builder.

The wire form is a protobuf ``TxBody`` with one ``Any`` message whose ``value`` field holds
the binding payload. This is the published protobuf field encoding (varint tags and
length-delimited fields), not a Cosmos SDK encoder; it never signs and holds no key. The
message type URL is a documented test vector.
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

_TYPE_URL = "/ezchain.binding.v1.Attach"


def _varint(value: int) -> bytes:
    if value < 0:
        raise ChainBindingDecodeError("a protobuf length is negative")
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _read_varint(raw: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for shift in (0, 7, 14, 21, 28):
        if offset >= len(raw):
            raise ChainBindingDecodeError("a protobuf varint is truncated")
        byte = raw[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            if shift and value < (1 << shift):
                raise ChainBindingDecodeError("a protobuf varint is non-canonical")
            return value, offset
    raise ChainBindingDecodeError("a protobuf varint is too long")


def _field(number: int, value: bytes) -> bytes:
    return _varint((number << 3) | 2) + _varint(len(value)) + value


def _fields(raw: bytes) -> list[tuple[int, int, bytes]]:
    """Read length-delimited protobuf fields, refusing truncated or malformed wire data."""
    out: list[tuple[int, int, bytes]] = []
    offset = 0
    while offset < len(raw):
        key, offset = _read_varint(raw, offset)
        number, wire = key >> 3, key & 0x07
        if number == 0 or wire != 2:
            raise ChainBindingDecodeError("the protobuf field is not length-delimited")
        size, offset = _read_varint(raw, offset)
        if offset + size > len(raw):
            raise ChainBindingDecodeError("a protobuf field is truncated")
        out.append((number, wire, raw[offset : offset + size]))
        offset += size
    return out


def build_cosmos_transaction(fields: DecodedFields) -> bytes:
    """Build an unsigned TxBody carrying the binding payload in an ``Any`` value."""
    payload = encode_binding_payload(fields)
    message = _field(1, _TYPE_URL.encode("ascii")) + _field(2, payload)
    return _field(1, message)


def decode_cosmos_transaction(raw: bytes) -> DecodedFields:
    """Decode an unsigned TxBody into the trust boundary's field map."""
    if not isinstance(raw, bytes) or not raw:
        raise ChainBindingDecodeError("the transaction is empty")
    body = _fields(raw)
    if len(body) != 1 or body[0][0] != 1:
        raise ChainBindingDecodeError("the transaction is not a single-message TxBody")
    parts = _fields(body[0][2])
    if len(parts) != 2 or parts[0][0] != 1 or parts[1][0] != 2:
        raise ChainBindingDecodeError("the message is not an Any with a value")
    data = parts[1][2]
    if not data.startswith(BINDING_MAGIC):
        raise ChainBindingDecodeError("the transaction has no binding payload")
    return replace(
        decode_binding_payload(data),
        version="proto-v1",
        size_bytes=len(raw),
        instruction_count=1,
    )


class CosmosBuilder:
    """Builds unsigned Cosmos transactions from an immutable approval only."""

    async def prepare(self, approved: ApprovedIntent) -> bytes:
        fields = fields_from_approval(approved, version="proto-v1")
        return build_cosmos_transaction(fields)


__all__ = ["CosmosBuilder", "build_cosmos_transaction", "decode_cosmos_transaction"]
