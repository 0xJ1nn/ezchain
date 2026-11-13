"""Binding a signature to the intent that was approved.

The approved intent is the only source of what may be signed. An adapter that builds the
unsigned transaction receives it and returns bytes; those bytes are decoded here and every
field a venue can substitute (program, cluster, mint, side, amount, minimum output,
recipient, idempotency key) is compared with the approval before any key is touched. A
disagreement is a typed reason code and no signature, never a best-effort match.

This module is pure: it holds the value objects, the canonical wire form the test signing
boundary speaks, the decode and the comparison. It never signs, never holds key material
and performs no I/O. The signer and the key live in ``services/executor``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, cast, runtime_checkable

from .execution import Order
from .types import Side

TRANSACTION_SCHEMA = "ezchain.transaction.v1"

# One code per thing a venue can change under the approval. The code is the stable
# identifier; the message is for display and may change.
TRANSACTION_NOT_APPROVABLE = "TRANSACTION_NOT_APPROVABLE"
TRANSACTION_UNDECODABLE = "TRANSACTION_UNDECODABLE"
TRANSACTION_PROGRAM_MISMATCH = "TRANSACTION_PROGRAM_MISMATCH"
TRANSACTION_CLUSTER_MISMATCH = "TRANSACTION_CLUSTER_MISMATCH"
TRANSACTION_MINT_MISMATCH = "TRANSACTION_MINT_MISMATCH"
TRANSACTION_SIDE_MISMATCH = "TRANSACTION_SIDE_MISMATCH"
TRANSACTION_AMOUNT_MISMATCH = "TRANSACTION_AMOUNT_MISMATCH"
TRANSACTION_MIN_OUT_MISMATCH = "TRANSACTION_MIN_OUT_MISMATCH"
TRANSACTION_RECIPIENT_MISMATCH = "TRANSACTION_RECIPIENT_MISMATCH"
TRANSACTION_IDEMPOTENCY_MISMATCH = "TRANSACTION_IDEMPOTENCY_MISMATCH"
TRANSACTION_DIGEST_MISMATCH = "TRANSACTION_DIGEST_MISMATCH"
TRANSACTION_UNSIGNED = "TRANSACTION_UNSIGNED"
TRANSACTION_BUILD_FAILED = "TRANSACTION_BUILD_FAILED"
TRANSACTION_SIGN_FAILED = "TRANSACTION_SIGN_FAILED"

BINDING_REASONS: tuple[str, ...] = (
    TRANSACTION_UNDECODABLE,
    TRANSACTION_PROGRAM_MISMATCH,
    TRANSACTION_CLUSTER_MISMATCH,
    TRANSACTION_MINT_MISMATCH,
    TRANSACTION_SIDE_MISMATCH,
    TRANSACTION_AMOUNT_MISMATCH,
    TRANSACTION_MIN_OUT_MISMATCH,
    TRANSACTION_RECIPIENT_MISMATCH,
    TRANSACTION_IDEMPOTENCY_MISMATCH,
    TRANSACTION_DIGEST_MISMATCH,
)


class TransactionDecodeError(ValueError):
    """The prepared bytes are not a transaction this boundary can read."""

    code = TRANSACTION_UNDECODABLE


def _canonical_decimal(value: Decimal) -> str:
    """One spelling per value, so ``1.50`` and ``1.5`` bind alike."""
    if not value.is_finite():
        raise ValueError("a bound amount cannot be a non-finite decimal")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _decode_decimal(payload: dict[str, Any], key: str) -> Decimal:
    value = payload.get(key)
    if not isinstance(value, str):
        raise TransactionDecodeError(f"{key} is not a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise TransactionDecodeError(f"{key} is not a decimal") from None
    if not parsed.is_finite():
        raise TransactionDecodeError(f"{key} is not finite")
    return parsed


def _require_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise TransactionDecodeError(f"{key} is missing")
    return value


@dataclass(frozen=True)
class ApprovedIntent:
    """The immutable approval a transaction must reproduce.

    ``amount`` is the approved input size and ``min_out`` the approved output floor, both
    in the units the side spends and receives: for a buy the amount is USD and the floor is
    the token quantity, for a sell the amount is the token quantity and the floor is USD.
    ``digest`` covers every field, so a later stage can carry one identifier for the whole
    approval instead of trusting a parameter list it did not see.
    """

    chain: str
    cluster: str
    mint: str
    side: Side
    amount: Decimal
    min_out: Decimal
    recipient: str
    program_id: str
    idempotency_key: str

    def __post_init__(self) -> None:
        if not self.chain:
            raise ValueError("an approved intent needs a chain")
        if not self.cluster:
            raise ValueError("an approved intent needs a cluster")
        if not self.mint:
            raise ValueError("an approved intent needs a mint")
        if self.side not in ("buy", "sell"):
            raise ValueError("an approved intent needs a side")
        if not self.amount.is_finite() or self.amount <= 0:
            raise ValueError("an approved intent needs a positive amount")
        if not self.min_out.is_finite() or self.min_out < 0:
            raise ValueError("an approved intent needs a non-negative output floor")
        if not self.recipient:
            raise ValueError("an approved intent needs a recipient")
        if not self.program_id:
            raise ValueError("an approved intent needs a program id")
        if not self.idempotency_key:
            raise ValueError("an approved intent needs an idempotency key")

    @property
    def digest(self) -> str:
        """SHA-256 of the canonical approval: the identifier carried to confirmation."""
        canonical = {
            "schema": TRANSACTION_SCHEMA,
            "chain": self.chain,
            "cluster": self.cluster,
            "mint": self.mint,
            "side": self.side,
            "amount": _canonical_decimal(self.amount),
            "min_out": _canonical_decimal(self.min_out),
            "recipient": self.recipient,
            "program_id": self.program_id,
            "idempotency_key": self.idempotency_key,
        }
        raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def from_order(
        cls,
        order: Order,
        *,
        cluster: str,
        recipient: str,
        program_id: str,
    ) -> ApprovedIntent:
        """The approval a priced order asks the venue to build from.

        A buy was approved as USD in and tokens out; a sell as tokens in and USD out. The
        quote's output is the floor the transaction may not go below.
        """
        if order.side == "buy":
            amount, min_out = order.amount_usd, order.quantity
        else:
            amount, min_out = order.quantity, order.amount_usd
        return cls(
            chain=order.chain,
            cluster=cluster,
            mint=order.token_address,
            side=order.side,
            amount=amount,
            min_out=min_out,
            recipient=recipient,
            program_id=program_id,
            idempotency_key=order.idempotency_key,
        )


@dataclass(frozen=True)
class TransactionPayload:
    """The fields of the transaction that a signature commits to."""

    cluster: str
    program_id: str
    mint: str
    side: Side
    amount: Decimal
    min_out: Decimal
    recipient: str
    idempotency_key: str

    def __post_init__(self) -> None:
        if self.side not in ("buy", "sell"):
            raise ValueError("a transaction payload needs a side")

    def to_dict(self) -> dict[str, str]:
        return {
            "schema": TRANSACTION_SCHEMA,
            "cluster": self.cluster,
            "program_id": self.program_id,
            "mint": self.mint,
            "side": self.side,
            "amount": _canonical_decimal(self.amount),
            "min_out": _canonical_decimal(self.min_out),
            "recipient": self.recipient,
            "idempotency_key": self.idempotency_key,
        }


@dataclass(frozen=True)
class PreparedTransaction:
    """The raw bytes an adapter built, plus the payload decoded from them."""

    raw: bytes
    payload: TransactionPayload

    def __post_init__(self) -> None:
        if not self.raw:
            raise ValueError("a prepared transaction cannot be empty")

    @property
    def payload_digest(self) -> str:
        """SHA-256 of the exact bytes that will be signed."""
        return hashlib.sha256(self.raw).hexdigest()


@dataclass(frozen=True)
class SignedTransaction:
    """Bytes a signer produced for one approval, with the digests to carry."""

    raw: bytes
    signature: bytes
    approved: ApprovedIntent
    payload_digest: str

    def __post_init__(self) -> None:
        if not self.raw:
            raise ValueError("a signed transaction cannot be empty")
        if not self.signature:
            raise ValueError("a signed transaction needs a signature")
        if hashlib.sha256(self.raw).hexdigest() != self.payload_digest:
            raise ValueError("the signed payload digest does not cover these bytes")

    @property
    def approval_digest(self) -> str:
        return self.approved.digest


def encode_transaction(payload: TransactionPayload) -> bytes:
    """The canonical bytes of the test signing boundary.

    A real chain adapter builds a chain-native message; the fake venue and its tests speak
    this versioned envelope so a decode can be exercised without a chain SDK.
    """
    raw = json.dumps(payload.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return raw.encode("utf-8")


def decode_transaction(raw: bytes) -> PreparedTransaction:
    """Decode prepared bytes into the payload the signer must bind.

    Anything the boundary cannot read raises :class:`TransactionDecodeError` and therefore
    produces no signature.
    """
    if not isinstance(raw, bytes) or not raw:
        raise TransactionDecodeError("the prepared transaction is empty")
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise TransactionDecodeError("the prepared transaction is not valid JSON") from None
    if not isinstance(decoded, dict):
        raise TransactionDecodeError("the prepared transaction is not an object")
    if decoded.get("schema") != TRANSACTION_SCHEMA:
        raise TransactionDecodeError("the prepared transaction has an unknown schema")
    side = _require_text(decoded, "side")
    if side not in ("buy", "sell"):
        raise TransactionDecodeError("the prepared transaction has an unknown side")
    amount = _decode_decimal(decoded, "amount")
    if amount <= 0:
        raise TransactionDecodeError("the prepared amount is not positive")
    min_out = _decode_decimal(decoded, "min_out")
    if min_out < 0:
        raise TransactionDecodeError("the prepared output floor is negative")
    payload = TransactionPayload(
        cluster=_require_text(decoded, "cluster"),
        program_id=_require_text(decoded, "program_id"),
        mint=_require_text(decoded, "mint"),
        side=cast(Side, side),
        amount=amount,
        min_out=min_out,
        recipient=_require_text(decoded, "recipient"),
        idempotency_key=_require_text(decoded, "idempotency_key"),
    )
    return PreparedTransaction(raw=raw, payload=payload)


def verify_prepared_transaction(
    prepared: PreparedTransaction, approved: ApprovedIntent
) -> str | None:
    """The first field the prepared transaction changed, or ``None`` when it is bound.

    Checked cheapest-first around what a venue controls: the program it calls, the cluster
    it runs on, then the trade itself. Decimal fields compare canonically, so ``10.0`` and
    ``10`` are the same approved value while any real movement fails.
    """
    payload = prepared.payload
    if payload.program_id != approved.program_id:
        return TRANSACTION_PROGRAM_MISMATCH
    if payload.cluster != approved.cluster:
        return TRANSACTION_CLUSTER_MISMATCH
    if payload.mint != approved.mint:
        return TRANSACTION_MINT_MISMATCH
    if payload.side != approved.side:
        return TRANSACTION_SIDE_MISMATCH
    if _canonical_decimal(payload.amount) != _canonical_decimal(approved.amount):
        return TRANSACTION_AMOUNT_MISMATCH
    if _canonical_decimal(payload.min_out) != _canonical_decimal(approved.min_out):
        return TRANSACTION_MIN_OUT_MISMATCH
    if payload.recipient != approved.recipient:
        return TRANSACTION_RECIPIENT_MISMATCH
    if payload.idempotency_key != approved.idempotency_key:
        return TRANSACTION_IDEMPOTENCY_MISMATCH
    return None


@runtime_checkable
class TransactionAdapter(Protocol):
    """Builds the unsigned transaction from the approval, and from nothing else."""

    async def prepare(self, approved: ApprovedIntent) -> bytes: ...


@runtime_checkable
class TransactionSigner(Protocol):
    """Turns a decoded, verified transaction into signed bytes."""

    def sign(
        self, prepared: PreparedTransaction, approved: ApprovedIntent
    ) -> SignedTransaction: ...


__all__ = [
    "BINDING_REASONS",
    "TRANSACTION_AMOUNT_MISMATCH",
    "TRANSACTION_BUILD_FAILED",
    "TRANSACTION_CLUSTER_MISMATCH",
    "TRANSACTION_DIGEST_MISMATCH",
    "TRANSACTION_IDEMPOTENCY_MISMATCH",
    "TRANSACTION_MIN_OUT_MISMATCH",
    "TRANSACTION_MINT_MISMATCH",
    "TRANSACTION_NOT_APPROVABLE",
    "TRANSACTION_PROGRAM_MISMATCH",
    "TRANSACTION_RECIPIENT_MISMATCH",
    "TRANSACTION_SCHEMA",
    "TRANSACTION_SIDE_MISMATCH",
    "TRANSACTION_SIGN_FAILED",
    "TRANSACTION_UNDECODABLE",
    "TRANSACTION_UNSIGNED",
    "ApprovedIntent",
    "PreparedTransaction",
    "SignedTransaction",
    "TransactionAdapter",
    "TransactionDecodeError",
    "TransactionPayload",
    "TransactionSigner",
    "decode_transaction",
    "encode_transaction",
    "verify_prepared_transaction",
]
