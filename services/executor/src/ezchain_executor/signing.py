"""The signing boundary: decode the prepared bytes, bind them, then sign.

The executor is the only key holder. This module keeps that boundary in one place: a
:class:`KeyRing` (the vault/solders seam) exposes a signature and never the secret, and
:class:`LocalTransactionSigner` refuses before touching the key when the decoded
transaction differs from the approved intent in any bound field.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ezchain_core import (
    TRANSACTION_UNSIGNED,
    ApprovedIntent,
    PreparedTransaction,
    SignedTransaction,
    TransactionAdapter,
    TransactionSigner,
    verify_prepared_transaction,
)


class SigningRefused(RuntimeError):
    """A signature was not produced. ``code`` is the reason the pipeline records."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


@runtime_checkable
class KeyRing(Protocol):
    """The vault seam: signs bytes and never exposes the private key."""

    def sign(self, payload: bytes) -> bytes: ...


@dataclass(frozen=True)
class LocalTransactionSigner:
    """Verify the decode, then sign the exact bytes that passed."""

    keyring: KeyRing

    def sign(self, prepared: PreparedTransaction, approved: ApprovedIntent) -> SignedTransaction:
        code = verify_prepared_transaction(prepared, approved)
        if code is not None:
            raise SigningRefused(code)
        signature = self.keyring.sign(prepared.raw)
        if not signature:
            raise SigningRefused(TRANSACTION_UNSIGNED)
        return SignedTransaction(
            raw=prepared.raw,
            signature=signature,
            approved=approved,
            payload_digest=prepared.payload_digest,
        )


@dataclass(frozen=True)
class SigningBoundary:
    """Everything the pipeline needs to turn an order into signed bytes.

    ``adapter`` sees the :class:`ApprovedIntent` and returns bytes; it has no access to the
    order or the quote, so it cannot build from a second source of truth. ``cluster``,
    ``recipient`` and ``program_id`` are the executor's own custody facts, checked against
    the decode rather than trusted from it.
    """

    cluster: str
    recipient: str
    program_id: str
    adapter: TransactionAdapter
    signer: TransactionSigner


__all__ = [
    "KeyRing",
    "LocalTransactionSigner",
    "SigningBoundary",
    "SigningRefused",
]
