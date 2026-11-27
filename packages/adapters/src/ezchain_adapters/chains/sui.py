"""Sui unsigned-transaction decoder and builder.

The wire form is a minimal BCS-serialized programmable transaction: a version byte, one
pure input holding the binding payload, and one MoveCall command referencing that input.
This is a documented subset of BCS, not a full Sui transaction encoder; it never signs and
holds no key. The package address is a non-secret test vector.
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

_PACKAGE = bytes((0x55,)) * 32
_VERSION = 1
_VARIANT_PURE = 0
_VARIANT_INPUT = 0
_COMMAND_MOVE_CALL = 0


def _uleb(value: int) -> bytes:
    if value < 0:
        raise ChainBindingDecodeError("a BCS length is negative")
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _read_uleb(raw: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for shift in (0, 7, 14, 21, 28):
        if offset >= len(raw):
            raise ChainBindingDecodeError("a BCS length is truncated")
        byte = raw[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            if shift and value < (1 << shift):
                raise ChainBindingDecodeError("a BCS length is non-canonical")
            return value, offset
    raise ChainBindingDecodeError("a BCS length is too long")


def _string(text: str) -> bytes:
    value = text.encode("ascii")
    return _uleb(len(value)) + value


def build_sui_transaction(fields: DecodedFields) -> bytes:
    """Build an unsigned BCS transaction carrying the binding payload in a pure input."""
    payload = encode_binding_payload(fields)
    out = bytearray(bytes((_VERSION,)))
    out += _uleb(1)  # one input
    out += (0).to_bytes(4, "little")  # Input::Pure variant
    out += _uleb(len(payload)) + payload
    out += _uleb(1)  # one command
    out += bytes((_COMMAND_MOVE_CALL,))
    out += _PACKAGE
    out += _string("binding")  # module
    out += _string("attach")  # function
    out += _uleb(0)  # no type arguments
    out += _uleb(1)  # one argument
    out += (0).to_bytes(4, "little") + _uleb(0)  # Argument::Input(0)
    return bytes(out)


def _read_string(raw: bytes, offset: int) -> tuple[str, int]:
    size, offset = _read_uleb(raw, offset)
    if offset + size > len(raw):
        raise ChainBindingDecodeError("a BCS string is truncated")
    try:
        return raw[offset : offset + size].decode("ascii"), offset + size
    except UnicodeDecodeError:
        raise ChainBindingDecodeError("a BCS string is not ASCII") from None


def decode_sui_transaction(raw: bytes) -> DecodedFields:
    """Decode an unsigned BCS transaction into the trust boundary's field map."""
    if not isinstance(raw, bytes) or not raw:
        raise ChainBindingDecodeError("the transaction is empty")
    if raw[0] != _VERSION:
        raise ChainBindingDecodeError("the transaction version is unsupported")
    offset = 1
    inputs, offset = _read_uleb(raw, offset)
    if inputs != 1:
        raise ChainBindingDecodeError("the input layout is unsupported")
    if offset + 4 > len(raw) or raw[offset : offset + 4] != bytes(4):
        raise ChainBindingDecodeError("the input is not a pure payload")
    offset += 4
    size, offset = _read_uleb(raw, offset)
    if offset + size > len(raw):
        raise ChainBindingDecodeError("the pure payload is truncated")
    data = raw[offset : offset + size]
    offset += size
    if not data.startswith(BINDING_MAGIC):
        raise ChainBindingDecodeError("the transaction has no binding payload")
    commands, offset = _read_uleb(raw, offset)
    if commands != 1 or offset >= len(raw) or raw[offset] != _COMMAND_MOVE_CALL:
        raise ChainBindingDecodeError("the command layout is unsupported")
    offset += 1
    if offset + 32 > len(raw):
        raise ChainBindingDecodeError("the package address is truncated")
    offset += 32
    _module, offset = _read_string(raw, offset)
    _function, offset = _read_string(raw, offset)
    types, offset = _read_uleb(raw, offset)
    if types != 0:
        raise ChainBindingDecodeError("type arguments are unsupported")
    args, offset = _read_uleb(raw, offset)
    if args != 1 or offset + 5 > len(raw) or raw[offset : offset + 4] != bytes(4):
        raise ChainBindingDecodeError("the command argument is unsupported")
    _index, offset = _read_uleb(raw, offset + 4)
    if offset != len(raw):
        raise ChainBindingDecodeError("the transaction has trailing bytes")
    return replace(
        decode_binding_payload(data),
        version="bcs-v1",
        size_bytes=len(raw),
        instruction_count=1,
    )


class SuiBuilder:
    """Builds unsigned BCS transactions from an immutable approval only."""

    async def prepare(self, approved: ApprovedIntent) -> bytes:
        fields = fields_from_approval(approved, version="bcs-v1")
        return build_sui_transaction(fields)


__all__ = ["SuiBuilder", "build_sui_transaction", "decode_sui_transaction"]
