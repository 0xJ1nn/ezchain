"""Bearer-token authentication for mutating HTTP routes.

Reads (`GET`/`HEAD`/`OPTIONS`) stay open: they expose no money movement and keep
health probes and the read-only UI simple. Every other method under ``/api/`` is denied
unless ``Authorization: Bearer <token>`` matches ``EZCHAIN_API_TOKEN``.

The check lives in middleware rather than in per-route dependencies so a route added
later is protected by default. A production process refuses to boot without a token;
a development process with no token permits mutations and logs one warning so the
existing local workflow keeps working.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import ASGIApp

from .config import Settings

logger = logging.getLogger(__name__)

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
PROTECTED_PREFIX = "/api/"
_warned_missing_token = False


def is_protected_request(method: str, path: str) -> bool:
    """True when the request can change state and therefore needs a token."""
    return method.upper() not in READ_METHODS and path.startswith(PROTECTED_PREFIX)


def _unauthorized(message: str) -> JSONResponse:
    # Same envelope as ``HTTPException(detail={...})`` so the web error parser works.
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


def _bearer_token(header: str | None) -> str | None:
    if header is None:
        return None
    scheme, separator, value = header.partition(" ")
    if not separator or scheme.lower() != "bearer" or not value:
        return None
    return value.strip() or None


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not is_protected_request(request.method, request.url.path):
            return await call_next(request)
        if not self._settings.auth_enabled:
            return await call_next(request)

        expected = (
            None
            if self._settings.api_token is None
            else self._settings.api_token.get_secret_value()
        )
        if expected is None:
            if self._settings.environment == "prod":
                # Fail closed even though Settings validation makes this unreachable.
                return _unauthorized("mutating requests require a bearer token")
            global _warned_missing_token
            if not _warned_missing_token:
                _warned_missing_token = True
                logger.warning(
                    "EZCHAIN_API_TOKEN is unset; allowing mutations in %s. "
                    "Set a token before exposing this process.",
                    self._settings.environment,
                )
            return await call_next(request)

        supplied = _bearer_token(request.headers.get("authorization"))
        # Compare bytes: hmac.compare_digest(str, str) raises TypeError for non-ASCII,
        # which would turn a malformed Authorization header into a 500.
        if supplied is None or not hmac.compare_digest(
            supplied.encode("utf-8"), expected.encode("utf-8")
        ):
            return _unauthorized("missing or invalid bearer token")
        return await call_next(request)


__all__ = [
    "PROTECTED_PREFIX",
    "READ_METHODS",
    "BearerAuthMiddleware",
    "is_protected_request",
]
