"""Decode-and-bind: the gate that runs before any signer is called.

Given the approved :class:`~ezchain_core.trust.intent.Intent` and either unsigned bytes
or an already-decoded field map, this module decodes once and compares every approved
field. A swapped mint, inflated amount, changed recipient, foreign program, wrong
cluster, wrong side, lowered floor, replaced key or wrong chain returns its own taxonomy
code as a :class:`~ezchain_core.trust.types.Refusal` (raised as
:class:`BindingRefused` from :func:`decode_and_bind`). There is no signer parameter in
this module at all, so a mismatch cannot reach a key by construction.

Decoding a chain-native transaction is an adapter's job; the versioned JSON envelope
here is the test/neutral wire form so the decode path is exercised without a chain SDK.
The comparison is deliberately stricter than "amount ≤ approved": every approved field
must be reproduced exactly, because the digest identifies those exact fields.

Nothing here verifies a signature, contacts a venue, computes money or writes anything.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from ..reason_codes import (
    TRANSACTION_AMOUNT_MISMATCH,
    TRANSACTION_CHAIN_MISMATCH,
    TRANSACTION_CLUSTER_MISMATCH,
    TRANSACTION_IDEMPOTENCY_MISMATCH,
    TRANSACTION_MIN_OUT_MISMATCH,
    TRANSACTION_MINT_MISMATCH,
    TRANSACTION_PROGRAM_MISMATCH,
    TRANSACTION_RECIPIENT_MISMATCH,
    TRANSACTION_SIDE_MISMATCH,
    TRANSACTION_UNDECODABLE,
)
from ..types import Side
from .intent import Intent
from .types import BINDING_FIELDS, Refusal, canonical_decimal, parse_exact_decimal

TRANSACTION_SCHEMA: Final = "ezchain.trust.transaction.v1"

# (field, code) in digest order. One source of truth for what a field's refusal is.
BINDING_FIELD_CODES: Final[tuple[tuple[str, str], ...]] = (
    ("chain", TRANSACTION_CHAIN_MISMATCH),
    ("cluster", TRANSACTION_CLUSTER_MISMATCH),
    ("mint", TRANSACTION_MINT_MISMATCH),
    ("side", TRANSACTION_SIDE_MISMATCH),
    ("amount", TRANSACTION_AMOUNT_MISMATCH),
    ("minimum_output", TRANSACTION_MIN_OUT_MISMATCH),
    ("recipient", TRANSACTION_RECIPIENT_MISMATCH),
    ("program_id", TRANSACTION_PROGRAM_MISMATCH),
    ("idempotency_key", TRANSACTION_IDEMPOTENCY_MISMATCH),
)

# Chain-native observations carried next to the approved fields for preflight. They are
# data the caller already decoded; the kernels never fetch or infer them.
_NATIVE_TEXT_FIELDS: Final = (
    "fee_payer",
    "blockhash",
    "version",
    "token_program_id",
    "token_owner",
    "allowance_spender",
)
_NATIVE_INT_FIELDS: Final = (
    "size_bytes",
    "account_count",
    "instruction_count",
    "token_decimals",
    "compute_units",
    "compute_unit_price",
)
_NATIVE_BOOL_FIELDS: Final = ("allowance_unlimited",)


class TransactionDecodeError(ValueError):
    """The bytes are not a transaction envelope this boundary can read."""

    code = TRANSACTION_UNDECODABLE


class BindingRefused(ValueError):
    """Raised by :func:`decode_and_bind` when the transaction is not the approval."""

    def __init__(self, refusal: Refusal) -> None:
        super().__init__(refusal.code)
        self.refusal = refusal


@dataclass(frozen=True)
class DecodedFields:
    """The fields decoded from an unsigned transaction, approved and observed alike."""

    chain: str
    cluster: str
    mint: str
    side: Side
    amount: Decimal
    minimum_output: Decimal
    recipient: str
    program_id: str
    idempotency_key: str
    fee_payer: str | None = None
    blockhash: str | None = None
    version: str | None = None
    size_bytes: int | None = None
    account_count: int | None = None
    instruction_count: int | None = None
    token_program_id: str | None = None
    token_owner: str | None = None
    token_decimals: int | None = None
    compute_units: int | None = None
    compute_unit_price: int | None = None
    allowance_unlimited: bool | None = None
    allowance_spender: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> DecodedFields:
        """Read a decoded field map, parsing the two money fields exactly.

        The nine approved fields are mandatory. Extra keys must be declared native
        observations with the right shape; an unknown or mistyped extra is a decode
        failure, never something that is silently ignored near money.
        """
        missing = [name for name in BINDING_FIELDS if name not in data]
        if missing:
            raise TransactionDecodeError(f"missing decoded field: {missing[0]}")
        known = (
            set(BINDING_FIELDS)
            | set(_NATIVE_TEXT_FIELDS)
            | set(_NATIVE_INT_FIELDS)
            | set(_NATIVE_BOOL_FIELDS)
        )
        unknown = set(data) - known
        if unknown:
            raise TransactionDecodeError(f"unknown decoded field: {sorted(unknown)[0]}")
        side = data["side"]
        if side not in ("buy", "sell"):
            raise TransactionDecodeError("the decoded side is not buy or sell")
        native: dict[str, Any] = {}
        for name in _NATIVE_TEXT_FIELDS:
            value = data.get(name)
            if value is not None and not isinstance(value, str):
                raise TransactionDecodeError(f"{name} is not text")
            native[name] = value
        for name in _NATIVE_INT_FIELDS:
            value = data.get(name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise TransactionDecodeError(f"{name} is not an integer")
            native[name] = value
        for name in _NATIVE_BOOL_FIELDS:
            value = data.get(name)
            if value is not None and not isinstance(value, bool):
                raise TransactionDecodeError(f"{name} is not a boolean")
            native[name] = value
        try:
            amount = parse_exact_decimal(data["amount"])
            minimum_output = parse_exact_decimal(data["minimum_output"])
        except ValueError:
            raise TransactionDecodeError("a decoded money field is not an exact decimal") from None
        if amount <= 0 or minimum_output < 0:
            raise TransactionDecodeError("the decoded amounts are out of range")
        return cls(
            chain=data["chain"],
            cluster=data["cluster"],
            mint=data["mint"],
            side=side,
            amount=amount,
            minimum_output=minimum_output,
            recipient=data["recipient"],
            program_id=data["program_id"],
            idempotency_key=data["idempotency_key"],
            **native,
        )

    def approved_view(self) -> dict[str, Any]:
        """Canonical wire spelling of every decoded field that is present.

        The nine approved fields are always present; chain-native observations are
        included when the caller decoded them, so a wire round-trip does not discard the
        facts preflight needs.
        """
        wire: dict[str, Any] = {
            "schema": TRANSACTION_SCHEMA,
            "chain": self.chain,
            "cluster": self.cluster,
            "mint": self.mint,
            "side": self.side,
            "amount": canonical_decimal(self.amount),
            "minimum_output": canonical_decimal(self.minimum_output),
            "recipient": self.recipient,
            "program_id": self.program_id,
            "idempotency_key": self.idempotency_key,
        }
        for name in (*_NATIVE_TEXT_FIELDS, *_NATIVE_INT_FIELDS, *_NATIVE_BOOL_FIELDS):
            value = getattr(self, name)
            if value is not None:
                wire[name] = value
        return wire


def encode_transaction(fields: DecodedFields) -> bytes:
    """Canonical bytes of the neutral wire envelope, for adapters and golden vectors."""
    raw = json.dumps(fields.approved_view(), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return raw.encode("utf-8")


def decode_transaction(raw: bytes) -> DecodedFields:
    """Decode unsigned transaction bytes into the field map the gate compares."""
    if not isinstance(raw, bytes) or not raw:
        raise TransactionDecodeError("the transaction bytes are empty")
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise TransactionDecodeError("the transaction is not valid JSON") from None
    if not isinstance(decoded, dict):
        raise TransactionDecodeError("the transaction is not an object")
    if decoded.get("schema") != TRANSACTION_SCHEMA:
        raise TransactionDecodeError("the transaction has an unknown schema")
    # The envelope's schema is not a transaction field; strip it before strict field
    # validation so an otherwise valid map is not refused for its own envelope tag.
    decoded.pop("schema", None)
    return DecodedFields.from_mapping(decoded)


def verify_binding(intent: Intent, fields: DecodedFields) -> Refusal | None:
    """The first approved field the transaction changed, or ``None`` when it is bound.

    Decimal fields compare by canonical spelling, so ``10.0`` and ``10`` are the same
    approved value while any real movement fails. Order is the digest's field order.
    """
    for name, code in BINDING_FIELD_CODES:
        approved = getattr(intent, name)
        observed = getattr(fields, name)
        if name in ("amount", "minimum_output"):
            matches = canonical_decimal(approved) == canonical_decimal(observed)
        else:
            matches = approved == observed
        if not matches:
            return Refusal(code=code, check=f"binding.{name}", detail=name)
    return None


def decode_and_bind(
    intent: Intent,
    *,
    raw: bytes | None = None,
    fields: Mapping[str, Any] | DecodedFields | None = None,
) -> DecodedFields:
    """Decode once, compare every field, and return the fields only when bound.

    Supply exactly one of ``raw`` (unsigned transaction bytes) or ``fields`` (an
    already-decoded field map). Either way the result is safe to hand to preflight and
    then to a signer: on any mismatch a :class:`BindingRefused` carrying a typed
    :class:`~ezchain_core.trust.types.Refusal` is raised and no bytes are returned.
    """
    if (raw is None) == (fields is None):
        raise ValueError("supply exactly one of raw bytes or a decoded field map")
    if raw is not None:
        decoded = decode_transaction(raw)
    elif isinstance(fields, DecodedFields):
        decoded = fields
    else:
        decoded = DecodedFields.from_mapping(fields or {})
    refusal = verify_binding(intent, decoded)
    if refusal is not None:
        raise BindingRefused(refusal)
    return decoded


def payload_digest(raw: bytes) -> str:
    """SHA-256 of the exact bytes that were bound and will be signed."""
    if not raw:
        raise ValueError("a payload digest needs non-empty bytes")
    return hashlib.sha256(raw).hexdigest()


__all__ = [
    "BINDING_FIELD_CODES",
    "TRANSACTION_SCHEMA",
    "BindingRefused",
    "DecodedFields",
    "TransactionDecodeError",
    "decode_and_bind",
    "decode_transaction",
    "encode_transaction",
    "payload_digest",
    "verify_binding",
]
