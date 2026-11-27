"""Solana unsigned-message decoder and builder.

The wire form is the published legacy Solana message: a compact-u16 signature count of
zero (unsigned), the three-byte message header, compact-u16 account keys, a 32-byte recent
blockhash and compact-u16 instructions. The binding payload is carried as the data of the
single memo instruction. This module parses those bytes; it never signs and never holds a
key. All key bytes are documented non-secret test vectors.
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

_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_FEE_PAYER_KEY = bytes((0x11,)) * 32
_PROGRAM_KEY = bytes((0x22,)) * 32
_TOKEN_PROGRAM_KEY = bytes((0x33,)) * 32
_BLOCKHASH = bytes((0x44,)) * 32
_MAX_KEYS = 64


def b58encode(raw: bytes) -> str:
    """Base58-encode bytes (the Solana address spelling), without external libraries."""
    if not raw:
        return ""
    number = int.from_bytes(raw, "big")
    out = ""
    while number:
        number, remainder = divmod(number, 58)
        out = _ALPHABET[remainder] + out
    leading = len(raw) - len(raw.lstrip(b"\x00"))
    return "1" * leading + out


def b58decode(text: str) -> bytes:
    """Decode a base58 string; a non-alphabet character is a decode failure."""
    number = 0
    for char in text:
        if char not in _ALPHABET:
            raise ChainBindingDecodeError("an account key is not base58")
        number = number * 58 + _ALPHABET.index(char)
    leading = len(text) - len(text.lstrip("1"))
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\x00" * leading + body


def compact_u16(value: int) -> bytes:
    """Solana's short-vector length prefix: one to three little-endian 7-bit groups."""
    if not 0 <= value <= 0xFFFF:
        raise ChainBindingDecodeError("a compact length is out of range")
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _read_compact_u16(raw: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for shift in (0, 7, 14):
        if offset >= len(raw):
            raise ChainBindingDecodeError("a compact length is truncated")
        byte = raw[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            if shift and value < (1 << shift):
                raise ChainBindingDecodeError("a compact length is non-canonical")
            return value, offset
    raise ChainBindingDecodeError("a compact length is malformed")


def build_solana_transaction(fields: DecodedFields) -> bytes:
    """Build the unsigned legacy message carrying ``fields`` in its memo instruction."""
    payload = encode_binding_payload(fields)
    out = bytearray(compact_u16(0))  # no signatures: this is an unsigned message
    out += bytes((1, 0, 1))  # one required signer, no readonly signed, one readonly unsigned
    out += compact_u16(3)
    out += _FEE_PAYER_KEY + _PROGRAM_KEY + _TOKEN_PROGRAM_KEY
    out += _BLOCKHASH
    out += compact_u16(1)  # one memo instruction
    out += bytes((1,))  # program id index: the program key
    out += compact_u16(0)  # no accounts in the memo
    out += compact_u16(len(payload))
    out += payload
    return bytes(out)


def decode_solana_transaction(raw: bytes) -> DecodedFields:
    """Decode an unsigned Solana message into the trust boundary's field map."""
    if not isinstance(raw, bytes) or not raw:
        raise ChainBindingDecodeError("the transaction is empty")
    signatures, offset = _read_compact_u16(raw, 0)
    if signatures != 0:
        raise ChainBindingDecodeError("a signed transaction is not an unsigned approval")
    if offset + 3 > len(raw):
        raise ChainBindingDecodeError("the message header is truncated")
    header = raw[offset : offset + 3]
    offset += 3
    if header[0] != 1:
        raise ChainBindingDecodeError("the signer set is not the approved wallet")
    count, offset = _read_compact_u16(raw, offset)
    if not 2 <= count <= _MAX_KEYS:
        raise ChainBindingDecodeError("the account list is outside policy")
    keys = []
    for _ in range(count):
        if offset + 32 > len(raw):
            raise ChainBindingDecodeError("an account key is truncated")
        keys.append(raw[offset : offset + 32])
        offset += 32
    if offset + 32 > len(raw):
        raise ChainBindingDecodeError("the blockhash is truncated")
    blockhash = raw[offset : offset + 32]
    offset += 32
    instructions, offset = _read_compact_u16(raw, offset)
    if instructions != 1:
        raise ChainBindingDecodeError("the instruction layout is unsupported")
    program_index = raw[offset] if offset < len(raw) else -1
    offset += 1
    if not 0 <= program_index < count:
        raise ChainBindingDecodeError("the instruction targets an unknown program")
    accounts, offset = _read_compact_u16(raw, offset)
    if accounts != 0:
        raise ChainBindingDecodeError("the binding instruction has unexpected accounts")
    size, offset = _read_compact_u16(raw, offset)
    if offset + size > len(raw):
        raise ChainBindingDecodeError("the binding instruction data is truncated")
    data = raw[offset : offset + size]
    offset += size
    if offset != len(raw) or not data.startswith(BINDING_MAGIC):
        raise ChainBindingDecodeError("the transaction has no single binding instruction")
    return replace(
        decode_binding_payload(data),
        fee_payer=b58encode(keys[0]),
        blockhash=b58encode(blockhash),
        version="legacy",
        size_bytes=len(raw),
        account_count=count,
        instruction_count=instructions,
        token_program_id=b58encode(keys[2]) if count > 2 else None,
    )


class SolanaBuilder:
    """Builds unsigned Solana messages from an immutable approval only."""

    async def prepare(self, approved: ApprovedIntent) -> bytes:
        fields = fields_from_approval(
            approved,
            fee_payer=b58encode(_FEE_PAYER_KEY),
            blockhash=b58encode(_BLOCKHASH),
            version="legacy",
            token_program_id=b58encode(_TOKEN_PROGRAM_KEY),
        )
        return build_solana_transaction(fields)


__all__ = [
    "SolanaBuilder",
    "b58decode",
    "b58encode",
    "build_solana_transaction",
    "compact_u16",
    "decode_solana_transaction",
]
