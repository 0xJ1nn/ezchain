"""The custody seam: one user's key handle goes in, a signature comes out.

Custody is the user's. A key is added at runtime by the person who owns it, encrypted at
rest under that person's credential, and used only for that person's orders. The platform's
job is to keep the key safe from everyone else — no plaintext key leaves the vault
boundary, no other principal can address the handle, no third party receives it — never to
gate the owner who asked for the trade.

This module is the contract, not the implementation. It holds the value objects, the
custody and automation vocabulary, the disclosure record and the typed refusal codes; it
never signs, never stores a key and performs no I/O. The signer, the encrypted store and
the hardware bridge live in ``services/executor``.

A handle is opaque and client-safe: an id, its owner, the custody tier that holds it, the
public address and a fingerprint. The fingerprint is a hash of the public key, never a key
(ADR-0040); it is what the ledger records for a fill.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from .signing import ApprovedIntent, PreparedTransaction, SignedTransaction

# The custody tiers a user may choose. ``hardware`` is one option, not the only path: a
# device approves each signature and the key never reaches the server. ``local_vault``
# keeps the key encrypted at rest under the user's credential. ``watch`` holds no key.
CUSTODY_LOCAL_VAULT = "local_vault"
CUSTODY_HARDWARE = "hardware"
CUSTODY_WATCH = "watch"
CUSTODY_TIERS: tuple[str, ...] = (CUSTODY_LOCAL_VAULT, CUSTODY_HARDWARE, CUSTODY_WATCH)
# A tier that can produce a signature. A watch-only handle can never sign, in any mode.
SIGNABLE_CUSTODY: frozenset[str] = frozenset({CUSTODY_LOCAL_VAULT, CUSTODY_HARDWARE})

Custody = Literal["local_vault", "hardware", "watch"]

# Per-user automation. ``manual`` is the approval queue: a person approves each order.
# ``full_auto`` is an explicit opt-in that skips per-order approval and never skips risk,
# the kill switch or the signed live gate.
AUTOMATION_MANUAL = "manual"
AUTOMATION_FULL = "full_auto"
AUTOMATION_TIERS: tuple[str, ...] = (AUTOMATION_MANUAL, AUTOMATION_FULL)

# The two places the product tells the user what they are doing. Both are recorded against
# the user; the vault line never asks for a confirmation the user already gave.
DISCLOSURE_KEY_ADD = "key_add"
DISCLOSURE_AUTOMATION_OPT_IN = "automation_opt_in"
DISCLOSURE_POINTS: tuple[str, ...] = (DISCLOSURE_KEY_ADD, DISCLOSURE_AUTOMATION_OPT_IN)

# One code per reason a signature was not produced. An unknown handle and another user's
# handle answer alike, so a caller cannot use the refusal to learn which handles exist.
VAULT_UNREACHABLE = "VAULT_UNREACHABLE"
VAULT_APPROVAL_DENIED = "VAULT_APPROVAL_DENIED"
VAULT_UNKNOWN_HANDLE = "VAULT_UNKNOWN_HANDLE"
VAULT_LOCKED = "VAULT_LOCKED"
VAULT_WATCH_ONLY = "VAULT_WATCH_ONLY"
VAULT_SIGN_FAILED = "VAULT_SIGN_FAILED"
# A non-paper pipeline needs the vault seam for the user's own handle.
VAULT_BOUNDARY_REQUIRED = "VAULT_BOUNDARY_REQUIRED"

VAULT_REASONS: tuple[str, ...] = (
    VAULT_UNREACHABLE,
    VAULT_APPROVAL_DENIED,
    VAULT_UNKNOWN_HANDLE,
    VAULT_LOCKED,
    VAULT_WATCH_ONLY,
    VAULT_SIGN_FAILED,
    VAULT_BOUNDARY_REQUIRED,
)

# Ed25519 is 64 bytes and a DER-encoded secp256k1 signature is at most 72; anything beyond
# this is not a signature for a transaction this project builds.
MAX_SIGNATURE_BYTES = 512

_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


def is_fingerprint(value: str) -> bool:
    """True when ``value`` is a SHA-256 hex digest and therefore a hash, never a key."""
    return isinstance(value, str) and _FINGERPRINT.match(value) is not None


class VaultRefused(RuntimeError):
    """No signature was produced. ``code`` is one of :data:`VAULT_REASONS`."""

    def __init__(self, code: str, message: str | None = None) -> None:
        if code not in VAULT_REASONS:
            raise ValueError(f"unknown vault reason: {code}")
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class KeyHandle:
    """One user's key, addressed without exposing it.

    ``handle_id`` is opaque and safe to log; ``owner`` is the principal that may use it;
    ``address`` and ``fingerprint`` are public facts. There is deliberately no field a
    private key, seed or passphrase could be stored in.
    """

    handle_id: str
    owner: str
    custody: Custody
    chain: str
    address: str
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.handle_id:
            raise ValueError("a key handle needs an id")
        if not self.owner:
            raise ValueError("a key handle belongs to a user")
        if self.custody not in CUSTODY_TIERS:
            raise ValueError(f"unknown custody tier: {self.custody!r}")
        if not self.chain:
            raise ValueError("a key handle needs a chain")
        if not self.address:
            raise ValueError("a key handle needs the public address it signs for")
        if not is_fingerprint(self.fingerprint):
            raise ValueError("a fingerprint is a SHA-256 hash of the public key, never a key")

    @property
    def signable(self) -> bool:
        return self.custody in SIGNABLE_CUSTODY


@dataclass(frozen=True)
class VaultSignature:
    """A signature over one prepared transaction, and nothing else.

    The fields are exactly what a caller may rely on: which handle signed, the fingerprint
    to carry to the ledger, and the signature bytes. A vault that returned key material
    would have nowhere to put it.
    """

    handle_id: str
    fingerprint: str
    signature: bytes

    def __post_init__(self) -> None:
        if not self.handle_id:
            raise ValueError("a signature names the handle that produced it")
        if not is_fingerprint(self.fingerprint):
            raise ValueError("a signature carries a key fingerprint, never a key")
        if not self.signature:
            raise ValueError("a signature cannot be empty")
        if len(self.signature) > MAX_SIGNATURE_BYTES:
            raise ValueError("the signature is larger than a transaction signature can be")


@dataclass(frozen=True)
class RiskDisclosure:
    """The user was told what the choice means, and when.

    Recorded at the two points the product discloses: adding a key (what custody of that
    key means) and opting into full automation (orders may be placed without a per-order
    approval). ``version`` names the disclosure text shown, so a later wording change does
    not rewrite what a user agreed to.
    """

    point: str
    user: str
    acknowledged_at: datetime
    version: str

    def __post_init__(self) -> None:
        if self.point not in DISCLOSURE_POINTS:
            raise ValueError(f"unknown disclosure point: {self.point!r}")
        if not self.user:
            raise ValueError("a disclosure is recorded against the user who read it")
        if not self.version:
            raise ValueError("a disclosure names the text version it showed")


@dataclass(frozen=True)
class AutomationSetting:
    """One user's automation tier, with the opt-in disclosure it requires.

    ``manual`` needs no opt-in disclosure: a person approves each order through the
    approval queue. ``full_auto`` is refused without the automation disclosure for that
    same user, so the platform never runs unattended orders for someone who was not told.
    """

    user: str
    tier: str
    disclosure: RiskDisclosure | None = None

    def __post_init__(self) -> None:
        if not self.user:
            raise ValueError("an automation setting belongs to a user")
        if self.tier not in AUTOMATION_TIERS:
            raise ValueError(f"unknown automation tier: {self.tier!r}")
        if self.tier == AUTOMATION_FULL:
            if self.disclosure is None:
                raise ValueError("full automation needs the automation opt-in disclosure")
            if self.disclosure.point != DISCLOSURE_AUTOMATION_OPT_IN:
                raise ValueError("full automation needs the automation opt-in disclosure")
            if self.disclosure.user != self.user:
                raise ValueError("the opt-in disclosure belongs to another user")

    @property
    def requires_approval(self) -> bool:
        """True when each order waits for a person: the manual tier."""
        return self.tier == AUTOMATION_MANUAL


@runtime_checkable
class Vault(Protocol):
    """Signs one prepared transaction with one user's own key.

    The caller has already bound the bytes to the approved intent and names the
    authenticated principal the order belongs to. A vault returns a signature or raises
    :class:`VaultRefused`; it has no operation that returns a key, a seed or a passphrase.
    """

    def sign(
        self, prepared: PreparedTransaction, handle: KeyHandle, *, owner: str
    ) -> VaultSignature: ...


@runtime_checkable
class VaultBackedSigner(Protocol):
    """A :class:`~ezchain_core.signing.TransactionSigner` that signs through the vault seam.

    The handle is the authenticated user's own key, so the pipeline can tell a real
    custody path from a bare in-memory signer: a non-paper pipeline requires this shape.
    """

    handle: KeyHandle

    def sign(
        self, prepared: PreparedTransaction, approved: ApprovedIntent
    ) -> SignedTransaction: ...


__all__ = [
    "AUTOMATION_FULL",
    "AUTOMATION_MANUAL",
    "AUTOMATION_TIERS",
    "CUSTODY_HARDWARE",
    "CUSTODY_LOCAL_VAULT",
    "CUSTODY_TIERS",
    "CUSTODY_WATCH",
    "DISCLOSURE_AUTOMATION_OPT_IN",
    "DISCLOSURE_KEY_ADD",
    "DISCLOSURE_POINTS",
    "MAX_SIGNATURE_BYTES",
    "SIGNABLE_CUSTODY",
    "VAULT_APPROVAL_DENIED",
    "VAULT_BOUNDARY_REQUIRED",
    "VAULT_LOCKED",
    "VAULT_REASONS",
    "VAULT_SIGN_FAILED",
    "VAULT_UNKNOWN_HANDLE",
    "VAULT_UNREACHABLE",
    "VAULT_WATCH_ONLY",
    "AutomationSetting",
    "Custody",
    "KeyHandle",
    "RiskDisclosure",
    "Vault",
    "VaultBackedSigner",
    "VaultRefused",
    "VaultSignature",
    "is_fingerprint",
]
