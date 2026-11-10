"""Workspace-scoped provider credentials, encrypted at rest.

The workspace supplies its own provider key (Jupiter today). The key is validated,
encrypted by the configured secret store and stored without the plaintext. Every read
path returns metadata and a non-reversible fingerprint; only :func:`resolve_secret`
returns the key, for a provider call, and that value is never logged or serialised.

A key belongs to exactly one workspace. Listing, revoking and resolving always filter by
``workspace_id``, so a shared database cannot expose one workspace's credentials to
another.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ezchain_core import utcnow

from ..models import ProviderKeyRow, new_id
from .provider_secrets import CipherUnavailable, SecretCipher

SUPPORTED_PROVIDERS = frozenset({"jupiter"})
_PROVIDER = re.compile(r"^[a-z][a-z0-9_-]{1,39}$")
MAX_SECRET_LENGTH = 4096
FINGERPRINT_HEX_CHARS = 32


class ProviderKeyError(ValueError):
    code = "PROVIDER_KEY_ERROR"
    status_code = 422
    retryable = False

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ProviderKeyInvalid(ProviderKeyError):
    code = "PROVIDER_KEY_INVALID"


class ProviderKeyProviderUnknown(ProviderKeyError):
    code = "PROVIDER_KEY_PROVIDER_UNKNOWN"


class ProviderKeyNotFound(ProviderKeyError):
    code = "PROVIDER_KEY_NOT_FOUND"
    status_code = 404


class ProviderKeyAlreadyExists(ProviderKeyError):
    """This workspace already has an active key for the provider.

    A second active row is not additive: the resolver reads the newest, so revoking that
    one would silently make the older key live again. Replacement must be asked for.
    """

    code = "PROVIDER_KEY_ALREADY_EXISTS"
    status_code = 409


class ProviderKeyCipherUnavailable(ProviderKeyError):
    code = "PROVIDER_KEY_ENCRYPTION_UNAVAILABLE"
    status_code = 503

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        # Mirrors the store: a backend that is momentarily unreachable may recover, a
        # missing identity or deleted entry fails identically on every retry.
        self.retryable = retryable


class ProviderKeyForgetFailed(ProviderKeyError):
    code = "PROVIDER_KEY_FORGET_FAILED"
    status_code = 503
    # The row keeps the reference until the delete lands, so the same request can be
    # replayed and complete the purge.
    retryable = True


def fingerprint(secret: str, pepper: str | None = None) -> str:
    """A stable display value that cannot be turned back into the key."""
    key = (pepper or "").encode("utf-8")
    digest = hmac.new(key, secret.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest[:FINGERPRINT_HEX_CHARS]


def _validated_workspace(workspace_id: str) -> str:
    value = workspace_id.strip()
    if not value or len(value) > 64:
        raise ProviderKeyInvalid("workspace_id must be 1-64 characters")
    return value


def _validated_provider(provider: str) -> str:
    value = provider.strip().lower()
    if not _PROVIDER.fullmatch(value):
        raise ProviderKeyInvalid("provider must be a lower-case provider slug")
    if value not in SUPPORTED_PROVIDERS:
        raise ProviderKeyProviderUnknown(
            f"provider {value!r} does not accept a stored key; "
            f"supported: {', '.join(sorted(SUPPORTED_PROVIDERS))}"
        )
    return value


def _validated_secret(secret: str) -> str:
    value = secret.strip()
    if not value or len(value) > MAX_SECRET_LENGTH:
        raise ProviderKeyInvalid(f"key must be 1-{MAX_SECRET_LENGTH} characters")
    if not value.isascii() or not value.isprintable() or any(c.isspace() for c in value):
        raise ProviderKeyInvalid("key must be printable ASCII without whitespace")
    return value


def _validated_label(label: str | None) -> str | None:
    if label is None:
        return None
    value = label.strip()
    if not value:
        return None
    if len(value) > 120 or not value.isprintable():
        raise ProviderKeyInvalid("label must be at most 120 printable characters")
    return value


def _active_key(session: Session, workspace_id: str, provider: str) -> ProviderKeyRow | None:
    """The one active key for this workspace and provider, if any."""
    return session.scalar(
        select(ProviderKeyRow).where(
            ProviderKeyRow.workspace_id == workspace_id,
            ProviderKeyRow.provider == provider,
            ProviderKeyRow.revoked_at.is_(None),
        )
    )


def _discard(cipher: SecretCipher, stored: str) -> None:
    """Best-effort removal of a secret that never reached a row.

    A failure here must not replace the error being reported: the caller needs the reason
    the create failed, and the orphan class this cannot close is documented.
    """
    try:
        cipher.forget(stored)
    except Exception:  # noqa: BLE001 - preserve the original failure
        pass


def create_key(
    session: Session,
    *,
    workspace_id: str,
    provider: str,
    secret: str,
    cipher: SecretCipher,
    label: str | None = None,
    fingerprint_pepper: str | None = None,
    replace: bool = False,
    forget: Callable[[str], None] | None = None,
) -> ProviderKeyRow:
    """Encrypt and store one key. The plaintext is not retained anywhere.

    A workspace may hold one active key per provider, and a create that would add a
    second one is refused unless ``replace`` is set. Replacement is destructive: the
    previous row is revoked and its external copy deleted through ``forget`` before the
    new key is stored, so a later revoke cannot bring the old key back.
    """
    workspace = _validated_workspace(workspace_id)
    provider_id = _validated_provider(provider)
    plaintext = _validated_secret(secret)
    # Every input is validated before the key is handed to an external store. A bad
    # label must not leave an orphaned secret in the keyring.
    label_value = _validated_label(label)
    active = _active_key(session, workspace, provider_id)
    if active is not None and not replace:
        raise ProviderKeyAlreadyExists(
            f"an active {provider_id} key already exists in this workspace; "
            "revoke it, or resend with replace=true to retire it"
        )
    fingerprint_value = fingerprint(plaintext, fingerprint_pepper)
    try:
        cipher.ensure_available()
        ciphertext = cipher.encrypt(plaintext)
    except CipherUnavailable as exc:
        raise ProviderKeyCipherUnavailable(exc.message, retryable=exc.retryable) from exc
    if active is not None:
        # Encrypting first means a store failure leaves the active key untouched. The
        # retirement then goes through the revoke path, which deletes the external copy
        # before the row stops pointing at it.
        if forget is None:
            _discard(cipher, ciphertext)
            raise ProviderKeyError("replacing an active key requires a delete hook")
        try:
            revoke_key(session, workspace_id=workspace, key_id=active.id, forget=forget)
        except Exception:
            _discard(cipher, ciphertext)
            raise
    row = ProviderKeyRow(
        id=new_id(),
        workspace_id=workspace,
        provider=provider_id,
        label=label_value,
        fingerprint=fingerprint_value,
        secret_ciphertext=ciphertext,
        created_at=utcnow(),
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError as exc:
        # Two creates raced and the other one won the database's partial unique index.
        # The loser's secret never belongs to a row, so remove it before refusing.
        session.rollback()
        _discard(cipher, ciphertext)
        raise ProviderKeyAlreadyExists(
            f"an active {provider_id} key already exists in this workspace"
        ) from exc
    except Exception:
        # The row did not land, so the external copy must not survive either.
        _discard(cipher, ciphertext)
        session.rollback()
        raise
    session.refresh(row)
    return row


def list_keys(session: Session, workspace_id: str) -> list[ProviderKeyRow]:
    workspace = _validated_workspace(workspace_id)
    statement = (
        select(ProviderKeyRow)
        .where(ProviderKeyRow.workspace_id == workspace)
        .order_by(ProviderKeyRow.created_at.desc())
    )
    return list(session.scalars(statement).all())


def resolve_secret(
    session: Session,
    *,
    workspace_id: str,
    provider: str,
    cipher: SecretCipher,
) -> str:
    """Return the active key for a provider call. The caller must not log the result."""
    workspace = _validated_workspace(workspace_id)
    provider_id = _validated_provider(provider)
    statement = (
        select(ProviderKeyRow)
        .where(
            ProviderKeyRow.workspace_id == workspace,
            ProviderKeyRow.provider == provider_id,
            ProviderKeyRow.revoked_at.is_(None),
            ProviderKeyRow.secret_ciphertext != "",
        )
        # The partial unique index allows one active row for the pair, so there is no
        # "newest" to choose. The limit only keeps an un-migrated database deterministic.
        .limit(1)
    )
    row = session.scalar(statement)
    if row is None:
        raise ProviderKeyNotFound(f"no active {provider_id} key in this workspace")
    try:
        secret = cipher.decrypt(row.secret_ciphertext)
    except CipherUnavailable as exc:
        raise ProviderKeyCipherUnavailable(exc.message, retryable=exc.retryable) from exc
    row.last_used_at = utcnow()
    session.commit()
    return secret


def revoke_key(
    session: Session,
    *,
    workspace_id: str,
    key_id: str,
    forget: Callable[[str], None],
) -> bool:
    """Revoke one key inside the workspace and clear its stored material.

    ``forget`` is bound to the row's stored prefix, not to the current configuration, so
    a key written by one backend is deleted through that backend.

    Revocation and deletion are two steps, and the row is the only record of where the
    secret lives. The row is committed as revoked first so the key stops being usable
    immediately, but its stored reference is kept until the external delete returns: a
    failure therefore leaves a revoked row that still points at the orphaned copy, and
    replaying the request finishes the purge instead of 404-ing on a key that is only
    half-revoked. The reference is cleared only after the delete succeeds.
    """
    workspace = _validated_workspace(workspace_id)
    row = session.get(ProviderKeyRow, key_id)
    if row is None or row.workspace_id != workspace:
        return False
    if row.revoked_at is None:
        row.revoked_at = utcnow()
        session.commit()
    stored = row.secret_ciphertext
    if not stored:
        # Already revoked and fully purged; a revoked key has no active state to report.
        return False
    try:
        forget(stored)
    except Exception as exc:  # noqa: BLE001 - every backend failure is a 503
        raise ProviderKeyForgetFailed(
            "the key is revoked in ezCHAIN, but the external store could not delete it"
        ) from exc
    row.secret_ciphertext = ""
    session.commit()
    return True


def public_key(row: ProviderKeyRow) -> dict[str, Any]:
    """Metadata safe to serialise. The ciphertext and plaintext never appear here."""
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "provider": row.provider,
        "label": row.label,
        "fingerprint": row.fingerprint,
        "created_at": _iso(row.created_at),
        "last_used_at": _iso(row.last_used_at),
        "revoked_at": _iso(row.revoked_at),
    }


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    moment = _aware(value)
    return None if moment is None else moment.isoformat()


__all__ = [
    "FINGERPRINT_HEX_CHARS",
    "ProviderKeyAlreadyExists",
    "ProviderKeyCipherUnavailable",
    "ProviderKeyError",
    "ProviderKeyForgetFailed",
    "ProviderKeyInvalid",
    "ProviderKeyNotFound",
    "ProviderKeyProviderUnknown",
    "SUPPORTED_PROVIDERS",
    "create_key",
    "fingerprint",
    "list_keys",
    "public_key",
    "resolve_secret",
    "revoke_key",
]
