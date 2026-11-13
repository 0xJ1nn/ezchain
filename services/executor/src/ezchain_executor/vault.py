"""Custody at the executor: one user's handle in, a signature out, or a typed refusal.

The key belongs to the user who added it. It is encrypted at rest under that user's
credential, addressed by an opaque handle that names its owner, and used only for that
owner's orders. The platform protects the key from everyone else — it never leaves this
boundary, no other principal can borrow the handle, no vendor sees it — and never pretends
to protect the user from their own decisions: the disclosure at key-add and at automation
opt-in is where that conversation happens (ADR-0040).

:class:`HardwareSignerVault` is the vendor-agnostic seam. It speaks one small interface,
:class:`SignerTransport`, and everything behind that interface — a USB device, a local
signer daemon on a unix socket, a remote signer — is interchangeable. Nothing here imports
a vendor SDK, holds a seed or logs a key: the only things that cross the seam are a handle
id, transaction bytes, a signature and a public fingerprint.

Fail closed: an unreachable signer, a person declining on the device, an unknown handle,
a locked vault, a watch-only handle or a device that answers for another key all produce
no signature and one stable code from :data:`ezchain_core.vault.VAULT_REASONS`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ezchain_core import (
    CUSTODY_OWNER_MISMATCH,
    VAULT_APPROVAL_DENIED,
    VAULT_LOCKED,
    VAULT_SIGN_FAILED,
    VAULT_UNKNOWN_HANDLE,
    VAULT_UNREACHABLE,
    VAULT_WATCH_ONLY,
    ApprovedIntent,
    KeyHandle,
    PreparedTransaction,
    SignedTransaction,
    Vault,
    VaultRefused,
    VaultSignature,
    verify_prepared_transaction,
)

from .signing import SigningRefused


class SignerTransportError(RuntimeError):
    """The signer layer could not or would not sign. ``code`` is a vault reason code."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


class SignerUnreachable(SignerTransportError):
    """The device or signer process did not answer."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(VAULT_UNREACHABLE, message)


class SignerApprovalDenied(SignerTransportError):
    """A person declined the transaction on the device."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(VAULT_APPROVAL_DENIED, message)


class SignerHandleUnknown(SignerTransportError):
    """The signer holds no key for that handle."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(VAULT_UNKNOWN_HANDLE, message)


class SignerLocked(SignerTransportError):
    """The signer is present but locked, so it will not sign."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(VAULT_LOCKED, message)


@dataclass(frozen=True)
class SignerSignature:
    """What a signer layer returns: the signature and the public key it belongs to.

    The fingerprint is a SHA-256 hash of the public key, so comparing it proves the device
    signed for the handle that was asked for without either side handling key material.
    """

    signature: bytes
    fingerprint: str


@runtime_checkable
class SignerTransport(Protocol):
    """The one call the executor makes into a signer process or a device.

    Implementations own the vendor-specific details (USB HID, a unix socket, a remote
    signer) and raise a :class:`SignerTransportError` subclass for every non-signature.
    They never return key material because there is nowhere to put it.
    """

    def sign(self, handle_id: str, payload: bytes) -> SignerSignature: ...


@dataclass(frozen=True)
class HardwareSignerVault:
    """The vault a user's handle is used through, over a swappable signer layer.

    ``owner`` is the authenticated principal whose order is being signed. A handle that
    belongs to someone else refuses with the same code as an unknown handle, so a caller
    cannot probe which handles exist. The signature that returns must name this handle's
    fingerprint; one for another key is refused rather than trusted.

    The executor never supplies ``owner`` itself: the pipeline passes the principal it took
    from the priced order, so this check is the last one behind the pipeline's own.
    """

    transport: SignerTransport

    def sign(
        self, prepared: PreparedTransaction, handle: KeyHandle, *, owner: str
    ) -> VaultSignature:
        if not owner or handle.owner != owner:
            raise VaultRefused(VAULT_UNKNOWN_HANDLE, "the handle does not belong to this user")
        if not handle.signable:
            raise VaultRefused(VAULT_WATCH_ONLY, "a watch-only handle cannot sign")
        if not isinstance(prepared.raw, bytes) or not prepared.raw:
            raise VaultRefused(VAULT_SIGN_FAILED, "there are no transaction bytes to sign")
        try:
            answer = self.transport.sign(handle.handle_id, prepared.raw)
        except SignerTransportError as exc:
            raise VaultRefused(exc.code) from None
        except Exception:  # noqa: BLE001 - a broken transport is a refusal, not a crash
            raise VaultRefused(VAULT_SIGN_FAILED) from None
        if answer.fingerprint != handle.fingerprint:
            raise VaultRefused(VAULT_SIGN_FAILED, "the signer answered for another key")
        if not answer.signature:
            raise VaultRefused(VAULT_SIGN_FAILED, "the signer returned no signature")
        return VaultSignature(
            handle_id=handle.handle_id,
            fingerprint=handle.fingerprint,
            signature=answer.signature,
        )


@dataclass(frozen=True)
class VaultTransactionSigner:
    """A :class:`~ezchain_core.signing.TransactionSigner` that signs through the vault.

    It binds the decoded bytes to the approval before the vault is touched (defence in
    depth behind the pipeline's own check), so a caller that skips the pipeline still
    cannot sign what was not approved. The handle it carries is what marks a pipeline as
    custody-backed: a non-paper pipeline refuses a signer without one.

    The owner is never constructor state. It is taken from the approval the pipeline built
    from the priced order, and a handle belonging to another principal refuses before the
    vault is called: an executor that trusted an owner field on itself could sign one
    user's order with another user's key.
    """

    vault: Vault
    handle: KeyHandle

    def sign(self, prepared: PreparedTransaction, approved: ApprovedIntent) -> SignedTransaction:
        code = verify_prepared_transaction(prepared, approved)
        if code is not None:
            raise SigningRefused(code)
        if not approved.actor or approved.actor != self.handle.owner:
            raise SigningRefused(CUSTODY_OWNER_MISMATCH)
        try:
            signed = self.vault.sign(prepared, self.handle, owner=approved.actor)
        except VaultRefused as exc:
            raise SigningRefused(exc.code) from None
        try:
            return SignedTransaction(
                raw=prepared.raw,
                signature=signed.signature,
                approved=approved,
                payload_digest=prepared.payload_digest,
            )
        except ValueError:
            raise SigningRefused(VAULT_SIGN_FAILED) from None

    @property
    def fingerprint(self) -> str:
        """The public fingerprint the ledger records for a fill (never a key)."""
        return self.handle.fingerprint


__all__ = [
    "HardwareSignerVault",
    "SignerApprovalDenied",
    "SignerHandleUnknown",
    "SignerLocked",
    "SignerSignature",
    "SignerTransport",
    "SignerTransportError",
    "SignerUnreachable",
    "VaultTransactionSigner",
]
