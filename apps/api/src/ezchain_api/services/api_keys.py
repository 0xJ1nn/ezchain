"""Scoped agent keys, hashed at rest and audited on use.

One ezCHAIN deployment is one paper workspace. A key names the agent or user acting
inside that workspace and carries only the scopes it needs:

* ``read`` — GET/HEAD endpoints, including ``require_auth_for_reads`` deployments;
* ``trade`` — paper orders, strategies, runs and backtests;
* ``admin`` — key management, plugin reload and the operator kill switch.

The plaintext token is returned once, on creation. Only a SHA-256 hash is stored. An
optional pepper (``EZCHAIN_API_KEY_PEPPER``) makes a database-only leak insufficient.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_core import utcnow

from ..models import ApiKeyRow, new_id

SCOPES = frozenset({"read", "trade", "admin"})
TOKEN_PREFIX = "ezk_"
LAST_USED_REFRESH_SECONDS = 60


class ApiKeyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Principal:
    subject: str
    scopes: frozenset[str]
    key_id: str | None = None
    legacy: bool = False

    def has_scope(self, scope: str) -> bool:
        return "admin" in self.scopes or scope in self.scopes


def normalize_scopes(scopes: Sequence[str] | None) -> tuple[str, ...]:
    if not scopes:
        raise ApiKeyError("API_KEY_SCOPES_REQUIRED", "at least one scope is required")
    unknown = sorted(set(scopes) - SCOPES)
    if unknown:
        raise ApiKeyError(
            "API_KEY_SCOPE_UNKNOWN",
            f"unknown scope(s): {', '.join(unknown)}; allowed: {', '.join(sorted(SCOPES))}",
        )
    # Admin implies the others; store the explicit grant so audit shows intent.
    return tuple(sorted(set(scopes)))


def hash_token(token: str, pepper: str | None = None) -> str:
    material = f"{pepper or ''}{token}".encode()
    return hashlib.sha256(material).hexdigest()


def create_key(
    session: Session,
    *,
    subject: str,
    scopes: Sequence[str],
    label: str | None = None,
    expires_in_days: int | None = 90,
    pepper: str | None = None,
) -> tuple[ApiKeyRow, str]:
    """Create a key and return ``(row, plaintext)``. The plaintext is never stored."""
    subject = subject.strip()
    if not subject or len(subject) > 120:
        raise ApiKeyError("API_KEY_SUBJECT_INVALID", "subject must be 1-120 characters")
    normalized = normalize_scopes(scopes)
    token = f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    now = utcnow()
    expires_at = None if expires_in_days is None else now + timedelta(days=expires_in_days)
    row = ApiKeyRow(
        id=new_id(),
        subject=subject,
        label=(label or "").strip() or None,
        token_hash=hash_token(token, pepper),
        scopes=list(normalized),
        created_at=now,
        expires_at=expires_at,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row, token


def verify_token(session: Session, token: str, pepper: str | None = None) -> ApiKeyRow | None:
    """Resolve a token to an active row. Unknown/revoked/expired tokens return ``None``."""
    if not token.startswith(TOKEN_PREFIX):
        return None
    row = session.scalar(
        select(ApiKeyRow).where(ApiKeyRow.token_hash == hash_token(token, pepper)).limit(1)
    )
    if row is None:
        return None
    now = utcnow()
    if row.revoked_at is not None:
        return None
    expires = _aware(row.expires_at)
    if expires is not None and expires <= now:
        return None
    last_used = _aware(row.last_used_at)
    if last_used is None or (now - last_used).total_seconds() >= LAST_USED_REFRESH_SECONDS:
        row.last_used_at = now
        session.commit()
    return row


def revoke_key(session: Session, key_id: str) -> bool:
    row = session.get(ApiKeyRow, key_id)
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = utcnow()
    session.commit()
    return True


def list_keys(session: Session) -> list[ApiKeyRow]:
    return list(session.scalars(select(ApiKeyRow).order_by(ApiKeyRow.created_at.desc())).all())


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def public_key(row: ApiKeyRow) -> dict[str, Any]:
    """Metadata safe to return to an operator. Never includes the hash or token."""
    return {
        "id": row.id,
        "subject": row.subject,
        "label": row.label,
        "scopes": list(row.scopes or []),
        "created_at": _iso(row.created_at),
        "last_used_at": _iso(row.last_used_at),
        "expires_at": _iso(row.expires_at),
        "revoked_at": _iso(row.revoked_at),
    }


def _iso(value: datetime | None) -> str | None:
    moment = _aware(value)
    return None if moment is None else moment.isoformat()


__all__ = [
    "ApiKeyError",
    "LAST_USED_REFRESH_SECONDS",
    "Principal",
    "SCOPES",
    "TOKEN_PREFIX",
    "create_key",
    "hash_token",
    "list_keys",
    "normalize_scopes",
    "public_key",
    "revoke_key",
    "verify_token",
]
