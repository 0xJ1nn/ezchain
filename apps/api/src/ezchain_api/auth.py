"""Bearer authentication and scope enforcement.

Reads stay open by default for a self-hosted workspace. Every non-read route under
``/api/`` requires a principal, either the deployment ``EZCHAIN_API_TOKEN`` (admin) or a
scoped key created through ``POST /api/v1/agent/keys``.

A hosted deployment that serves agents should set ``EZCHAIN_REQUIRE_AUTH_FOR_READS=true``
so even reads need a ``read`` scope. Health, readiness and metrics are outside ``/api``
and stay available to the orchestrator.

The check lives in middleware rather than per-route dependencies so a route added later
is protected by default. A development process with no token permits mutations and logs
one warning so the existing local workflow keeps working.
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import ASGIApp

from .config import Settings
from .services import api_keys

logger = logging.getLogger(__name__)

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
PROTECTED_PREFIX = "/api/"
READ_ONLY_POST_PATHS = frozenset(
    {
        "/api/v1/graph/validate",
        "/api/v1/graph/dry-run",
    }
)
_warned_missing_token = False


def is_protected_request(method: str, path: str) -> bool:
    """True when the request can change state and therefore needs a principal."""
    return method.upper() not in READ_METHODS and path.startswith(PROTECTED_PREFIX)


def requires_auth(method: str, path: str, settings: Settings) -> bool:
    if path.startswith("/api/v1/agent/keys"):
        return True
    if is_protected_request(method, path):
        return True
    return (
        settings.require_auth_for_reads
        and method.upper() in READ_METHODS
        and path.startswith(PROTECTED_PREFIX)
    )


def required_scope(method: str, path: str) -> str:
    """The least privilege a route requires. Admin routes win over the method."""
    if path.startswith("/api/v1/agent/keys"):
        return "admin"
    if method.upper() in READ_METHODS:
        return "read"
    if path.startswith("/api/v1/plugins/reload"):
        return "admin"
    if path.startswith("/api/v1/system/"):
        return "admin"
    if path in READ_ONLY_POST_PATHS:
        return "read"
    if path.startswith("/api/v1/plugins/") and path.endswith("/validate"):
        return "read"
    return "trade"


def _unauthorized(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={
            "detail": {
                "code": "UNAUTHORIZED",
                "message": message,
                "retryable": False,
            }
        },
        headers={"WWW-Authenticate": "Bearer"},
    )


def _forbidden(scope: str) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={
            "detail": {
                "code": "FORBIDDEN",
                "message": f"this principal lacks the {scope!r} scope",
                "retryable": False,
            }
        },
    )


def _bearer_token(header: str | None) -> str | None:
    if header is None:
        return None
    scheme, separator, value = header.partition(" ")
    if not separator or scheme.lower() != "bearer" or not value:
        return None
    return value.strip() or None


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        settings: Settings,
        session_factory: Callable[[], Session] | None = None,
    ) -> None:
        super().__init__(app)
        self._settings = settings
        self._session_factory = session_factory

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not requires_auth(request.method, request.url.path, self._settings):
            return await call_next(request)
        if not self._settings.auth_enabled:
            return await call_next(request)

        expected = (
            None
            if self._settings.api_token is None
            else self._settings.api_token.get_secret_value()
        )
        supplied = _bearer_token(request.headers.get("authorization"))

        principal: api_keys.Principal | None = None
        if (
            supplied is not None
            and expected is not None
            and hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))
        ):
            principal = api_keys.Principal(
                subject="deployment",
                scopes=frozenset(api_keys.SCOPES),
                legacy=True,
            )
        elif supplied is not None:
            principal = self._resolve_key(supplied)

        if principal is None:
            if supplied is not None:
                return _unauthorized("missing or invalid bearer token")
            if expected is None and self._settings.environment != "prod":
                global _warned_missing_token
                if not _warned_missing_token:
                    _warned_missing_token = True
                    logger.warning(
                        "EZCHAIN_API_TOKEN is unset; allowing mutations in %s. "
                        "Set a token before exposing this process.",
                        self._settings.environment,
                    )
                return await call_next(request)
            return _unauthorized("missing or invalid bearer token")

        scope = required_scope(request.method, request.url.path)
        if not principal.has_scope(scope):
            return _forbidden(scope)
        request.state.principal = principal
        return await call_next(request)

    def _resolve_key(self, token: str) -> api_keys.Principal | None:
        if self._session_factory is None:
            return None
        pepper = (
            None
            if self._settings.api_key_pepper is None
            else self._settings.api_key_pepper.get_secret_value()
        )
        try:
            with self._session_factory() as session:
                row = api_keys.verify_token(session, token, pepper)
        except Exception:
            logger.exception("API key verification failed; denying the request")
            return None
        if row is None:
            return None
        return api_keys.Principal(
            subject=row.subject,
            scopes=frozenset(row.scopes or []),
            key_id=row.id,
        )


__all__ = [
    "PROTECTED_PREFIX",
    "READ_METHODS",
    "READ_ONLY_POST_PATHS",
    "BearerAuthMiddleware",
    "is_protected_request",
    "required_scope",
    "requires_auth",
]
