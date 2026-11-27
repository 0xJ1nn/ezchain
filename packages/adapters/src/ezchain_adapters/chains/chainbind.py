"""The canonical binary binding payload carried inside a chain-native transaction.

A chain decoder's job is to read a real unsigned transaction and hand the nine approved
fields to :func:`ezchain_core.trust.decode_and_bind`. The transaction carries those fields
as one versioned, length-prefixed binary payload; this module is the one encoder/decoder of
that payload. It is deliberately independent of the neutral JSON envelope, which remains a
separate test/neutral wire form. Nothing here signs, holds a key, contacts a venue or
computes money: values are parsed exactly and compared by the trust kernels.

The wire form is ``MAGIC | nine length-prefixed UTF-8 fields | native observations``. Every
length is big-endian u16 so a field value cannot bleed into the next one. Native observations
are optional and type-tagged; an unknown tag or name is a decode failure, never a silent
drop near money.
"""

from __future__ import annotations

import struct
from typing import Any, Final, cast

from ezchain_core import ApprovedIntent
from ezchain_core.reason_codes import TRANSACTION_UNDECODABLE
from ezchain_core.trust import (
    BINDING_FIELDS,
    DecodedFields,
    TransactionDecodeError,
    canonical_decimal,
    parse_exact_decimal,
)

BINDING_MAGIC: Final = b"EZBTX001"
_U16: Final = struct.Struct(">H")
_I64: Final = struct.Struct(">q")

# Types and order mirror the trust kernel's declared native observations. Keeping the
# spelling here (rather than importing a private tuple) means a wire change is a guarded,
# visible change; ``DecodedFields.from_mapping`` is the final authority on what is allowed.
_TEXT_FIELDS: Final = (
    "fee_payer",
    "blockhash",
    "version",
    "token_program_id",
    "token_owner",
    "allowance_spender",
)
_INT_FIELDS: Final = (
    "size_bytes",
    "account_count",
    "instruction_count",
    "token_decimals",
    "compute_units",
    "compute_unit_price",
)
_BOOL_FIELDS: Final = ("allowance_unlimited",)
_TAG_TEXT: Final = 1
_TAG_INT: Final = 2
_TAG_BOOL: Final = 3


class ChainBindingDecodeError(ValueError):
    """The embedded binding payload is not one this boundary can read."""

    code = TRANSACTION_UNDECODABLE


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ChainBindingDecodeError(f"binding field {name} is not text")
    return value


def _read_chunk(raw: bytes, offset: int) -> tuple[bytes, int]:
    if offset + _U16.size > len(raw):
        raise ChainBindingDecodeError("a binding field length is truncated")
    (size,) = _U16.unpack_from(raw, offset)
    start = offset + _U16.size
    end = start + size
    if end > len(raw):
        raise ChainBindingDecodeError("a binding field value is truncated")
    return raw[start:end], end


def _write_chunk(raw: bytes) -> bytes:
    if len(raw) > 0xFFFF:
        raise ChainBindingDecodeError("a binding field is too long")
    return _U16.pack(len(raw)) + raw


def encode_binding_payload(fields: DecodedFields) -> bytes:
    """Serialize one decoded field map as the versioned binary payload.

    Decimals use their canonical spelling, so ``250.0`` and ``250.000`` are one value on
    the wire. Encoding is total for a valid :class:`DecodedFields`; only a pathologically
    long field can fail.
    """
    out = bytearray(BINDING_MAGIC)
    for name in BINDING_FIELDS:
        if name in ("amount", "minimum_output"):
            value = canonical_decimal(getattr(fields, name)).encode("ascii")
        else:
            value = _text(getattr(fields, name), name).encode("utf-8")
        out += _write_chunk(value)
    native: list[tuple[int, str, object]] = []
    for name in _TEXT_FIELDS:
        observed = getattr(fields, name)
        if observed is not None:
            native.append((_TAG_TEXT, name, _text(observed, name)))
    for name in _INT_FIELDS:
        observed = getattr(fields, name)
        if observed is not None:
            if isinstance(observed, bool) or not isinstance(observed, int):
                raise ChainBindingDecodeError(f"binding field {name} is not an integer")
            native.append((_TAG_INT, name, observed))
    for name in _BOOL_FIELDS:
        observed = getattr(fields, name)
        if observed is not None:
            if not isinstance(observed, bool):
                raise ChainBindingDecodeError(f"binding field {name} is not a boolean")
            native.append((_TAG_BOOL, name, observed))
    out += _U16.pack(len(native))
    for tag, name, observed in native:
        out += bytes((tag,)) + _write_chunk(name.encode("ascii"))
        if tag == _TAG_TEXT:
            out += _write_chunk(str(observed).encode("utf-8"))
        elif tag == _TAG_INT:
            out += _I64.pack(cast(int, observed))
        else:
            out += bytes((1 if observed else 0,))
    return bytes(out)


def decode_binding_payload(raw: bytes) -> DecodedFields:
    """Parse the binary payload into the field map the trust boundary consumes.

    Truncation, an unknown tag, a repeated field or a value the trust kernel refuses all
    raise :class:`ChainBindingDecodeError`, whose ``code`` is ``TRANSACTION_UNDECODABLE``.
    No partial field map is ever returned.
    """
    if not isinstance(raw, bytes) or not raw.startswith(BINDING_MAGIC):
        raise ChainBindingDecodeError("the binding payload has an unknown magic")
    offset = len(BINDING_MAGIC)
    values: dict[str, Any] = {}
    for name in BINDING_FIELDS:
        chunk, offset = _read_chunk(raw, offset)
        if name in ("amount", "minimum_output"):
            try:
                values[name] = parse_exact_decimal(chunk.decode("ascii"))
            except (UnicodeDecodeError, ValueError):
                raise ChainBindingDecodeError(f"binding field {name} is not a decimal") from None
        else:
            try:
                values[name] = chunk.decode("utf-8")
            except UnicodeDecodeError:
                raise ChainBindingDecodeError(f"binding field {name} is not UTF-8") from None
    if offset + _U16.size > len(raw):
        raise ChainBindingDecodeError("the native section is truncated")
    (count,) = _U16.unpack_from(raw, offset)
    offset += _U16.size
    seen: set[str] = set()
    for _ in range(count):
        if offset >= len(raw):
            raise ChainBindingDecodeError("a native field tag is truncated")
        tag = raw[offset]
        offset += 1
        name_bytes, offset = _read_chunk(raw, offset)
        try:
            name = name_bytes.decode("ascii")
        except UnicodeDecodeError:
            raise ChainBindingDecodeError("a native field name is not ASCII") from None
        if name in seen or name in values:
            raise ChainBindingDecodeError(f"binding field {name} is repeated")
        seen.add(name)
        if tag == _TAG_TEXT and name in _TEXT_FIELDS:
            value_bytes, offset = _read_chunk(raw, offset)
            try:
                values[name] = value_bytes.decode("utf-8")
            except UnicodeDecodeError:
                raise ChainBindingDecodeError(f"binding field {name} is not UTF-8") from None
        elif tag == _TAG_INT and name in _INT_FIELDS:
            if offset + _I64.size > len(raw):
                raise ChainBindingDecodeError(f"binding field {name} is truncated")
            (number,) = _I64.unpack_from(raw, offset)
            offset += _I64.size
            values[name] = number
        elif tag == _TAG_BOOL and name in _BOOL_FIELDS:
            if offset >= len(raw):
                raise ChainBindingDecodeError(f"binding field {name} is truncated")
            values[name] = bool(raw[offset])
            offset += 1
        else:
            raise ChainBindingDecodeError(f"binding field {name} has an unknown type tag")
    if offset != len(raw):
        raise ChainBindingDecodeError("the binding payload has trailing bytes")
    try:
        return DecodedFields.from_mapping(values)
    except TransactionDecodeError as exc:
        raise ChainBindingDecodeError(str(exc)) from None


def fields_from_approval(approved: ApprovedIntent, **native: object) -> DecodedFields:
    """Build a field map from an immutable approval plus caller observations.

    This is the sole adapter from the executor's approval object to the trust kernel's nine
    fields, so a builder's payload and the approval can never come from two sources. The
    two types share nine field names except the output floor (``min_out`` here,
    ``minimum_output`` in the trust kernel).
    """
    data: dict[str, Any] = {
        "chain": approved.chain,
        "cluster": approved.cluster,
        "mint": approved.mint,
        "side": approved.side,
        "amount": approved.amount,
        "minimum_output": approved.min_out,
        "recipient": approved.recipient,
        "program_id": approved.program_id,
        "idempotency_key": approved.idempotency_key,
    }
    data.update({key: value for key, value in native.items() if value is not None})
    return DecodedFields.from_mapping(data)


__all__ = [
    "BINDING_MAGIC",
    "ChainBindingDecodeError",
    "decode_binding_payload",
    "encode_binding_payload",
    "fields_from_approval",
]
