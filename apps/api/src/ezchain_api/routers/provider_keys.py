"""Workspace-scoped provider credential management.

The API accepts a provider key, stores it through the configured secret store, and
returns only a fingerprint. Listing and revoking work on metadata. No route returns the
key, and the key is never written to a log line or an error body.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, SecretStr
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..deps import get_secret_cipher, get_session
from ..services import provider_keys
from ..services.provider_secrets import SecretCipher

router = APIRouter(prefix="/api/v1/provider-keys", tags=["provider-keys"])


class ProviderKeyIn(BaseModel):
    provider: str
    key: SecretStr
    label: str | None = None


def _fingerprint_pepper(settings: Settings) -> str | None:
    configured = (
        settings.provider_key_fingerprint_pepper
        if settings.provider_key_fingerprint_pepper is not None
        else settings.api_key_pepper
    )
    return None if configured is None else configured.get_secret_value()


def _http_error(exc: provider_keys.ProviderKeyError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={
            "code": exc.code,
            "message": exc.message,
            "retryable": exc.retryable,
        },
    )


@router.get("")
def list_provider_keys(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[dict[str, Any]]:
    """Metadata for every key in this workspace. Fingerprints only, never the key."""
    rows = provider_keys.list_keys(session, workspace_id=settings.workspace_id)
    return [provider_keys.public_key(row) for row in rows]


@router.post("", status_code=201)
def create_provider_key(
    payload: ProviderKeyIn,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    cipher: SecretCipher = Depends(get_secret_cipher),
) -> dict[str, Any]:
    """Store a user-supplied key and return its fingerprint, not the key."""
    try:
        row = provider_keys.create_key(
            session,
            workspace_id=settings.workspace_id,
            provider=payload.provider,
            secret=payload.key.get_secret_value(),
            label=payload.label,
            cipher=cipher,
            fingerprint_pepper=_fingerprint_pepper(settings),
        )
    except provider_keys.ProviderKeyError as exc:
        raise _http_error(exc) from exc
    return {
        **provider_keys.public_key(row),
        "stored": True,
        "message": "the key is stored encrypted and cannot be read back through this API",
    }


@router.delete("/{key_id}", status_code=204)
def revoke_provider_key(
    key_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    cipher: SecretCipher = Depends(get_secret_cipher),
) -> Response:
    revoked = provider_keys.revoke_key(
        session,
        workspace_id=settings.workspace_id,
        key_id=key_id,
        cipher=cipher,
    )
    if not revoked:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "PROVIDER_KEY_NOT_FOUND",
                "message": "unknown or revoked key in this workspace",
                "retryable": False,
            },
        )
    return Response(status_code=204)
