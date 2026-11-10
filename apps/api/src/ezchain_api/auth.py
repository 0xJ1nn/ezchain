"""Bearer authentication and operational endpoint boundaries.

Reads stay open by default for a self-hosted workspace. Every non-read route under
``/api/`` requires a principal, either the deployment ``EZCHAIN_API_TOKEN`` (admin) or a
scoped key created through ``POST /api/v1/agent/keys``.

A hosted deployment that serves agents should set ``EZCHAIN_REQUIRE_AUTH_FOR_READS=true``
so even reads need a ``read`` scope.

The endpoints at the API root fall into four classes. ``/healthz`` and ``/readyz`` are
public orchestration probes: they carry no ledger data and answer without a credential.
``/health`` and ``/metrics`` describe the process, the scheduler and paper order counts, so
they are served only to a loopback client or to a request carrying the deployment token.
The interactive schema under ``/docs``, ``/redoc`` and ``/openapi.json`` is a developer
surface and is loopback-only. ``/api/v1/agent/state`` aggregates the whole workspace, so it
needs a principal even when ordinary reads are open.

The client address is the TCP peer. ``X-Forwarded-For`` is read only when that peer is
loopback or listed in ``EZCHAIN_TRUSTED_PROXY_CIDRS``, which is empty by default; the
rightmost address the nearest proxy did not add is the client. A remote client cannot
claim loopback with a spoofed header.

The check lives in middleware rather than per-route dependencies so a route added later
is protected by default. A development process with no token permits protected requests
and logs one warning so the existing local workflow keeps working. That fallback never
applies to a non-loopback request for an operational endpoint: those fail closed.
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
from collections.abc import Callable, Iterable

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
# Process and ledger diagnostics. A loopback client may read them without a token;
# every other client needs the deployment token.
OPERATIONAL_PATHS = frozenset({"/health", "/metrics"})
# The interactive schema reveals every route and is not a product surface.
LOOPBACK_ONLY_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"})
# Reads that aggregate the workspace need a principal even when ordinary reads are open.
AUTHENTICATED_READ_PATHS = frozenset({"/api/v1/agent/state"})
_warned_missing_token = False


def _path_key(path: str) -> str:
    """Classify a path independently of a trailing slash."""
    return path.rstrip("/") or "/"


def _address(value: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    if not value:
        return None
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _in_networks(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address, networks: Iterable[str]
) -> bool:
    for value in networks:
        try:
            if address in ipaddress.ip_network(value, strict=False):
                return True
        except ValueError:
            continue
    return False


def client_host(request: Request, settings: Settings) -> str | None:
    """The address used for the loopback test.

    The direct peer is authoritative unless it is loopback or a configured proxy. In
    those cases ``X-Forwarded-For`` is walked from the right, past loopback and trusted
    proxy hops, to the first address the nearest proxy did not add. A malformed hop is
    returned as-is so the loopback test fails closed.

    Reading the header from a loopback peer matters: a reverse proxy on the same host
    forwards public traffic from 127.0.0.1. Using the header there means a public client
    behind that proxy is still classified as remote. A remote peer can never reach this
    branch, so a spoofed header alone does not grant loopback.
    """
    peer = request.client.host if request.client is not None else None
    peer_address = _address(peer)
    if peer_address is None:
        return peer
    trusted = _in_networks(peer_address, settings.trusted_proxy_cidrs)
    if not peer_address.is_loopback and not trusted:
        return peer

    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return peer
    hops = [part.strip() for part in forwarded.split(",") if part.strip()]
    local_hop: str | None = None
    for hop in reversed(hops):
        hop_address = _address(hop)
        if hop_address is None:
            return hop
        if hop_address.is_loopback:
            local_hop = hop
            continue
        if _in_networks(hop_address, settings.trusted_proxy_cidrs):
            continue
        return hop
    return local_hop or peer


def is_loopback_client(request: Request, settings: Settings) -> bool:
    address = _address(client_host(request, settings))
    return address is not None and address.is_loopback


def is_protected_request(method: str, path: str) -> bool:
    """True when the request can change state and therefore needs a principal."""
    return method.upper() not in READ_METHODS and path.startswith(PROTECTED_PREFIX)


def requires_auth(method: str, path: str, settings: Settings) -> bool:
    key = _path_key(path)
    if key.startswith("/api/v1/agent/keys"):
        return True
    if key in AUTHENTICATED_READ_PATHS:
        return True
    if key in OPERATIONAL_PATHS:
        return True
    if is_protected_request(method, path):
        return True
    return (
        settings.require_auth_for_reads
        and method.upper() in READ_METHODS
        and key.startswith(PROTECTED_PREFIX)
    )


def required_scope(method: str, path: str) -> str:
    """The least privilege a route requires. Admin routes win over the method."""
    key = _path_key(path)
    if key.startswith("/api/v1/agent/keys"):
        return "admin"
    if key in OPERATIONAL_PATHS:
        return "admin"
    if method.upper() in READ_METHODS:
        return "read"
    if key.startswith("/api/v1/plugins/reload"):
        return "admin"
    if key.startswith("/api/v1/system/"):
        return "admin"
    if key in READ_ONLY_POST_PATHS:
        return "read"
    if key.startswith("/api/v1/plugins/") and key.endswith("/validate"):
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


def _not_found() -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "Not Found"})


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
        path = request.url.path
        key = _path_key(path)
        # Product routes do not pay for address parsing or a forwarded-header read.
        needs_loopback = key in LOOPBACK_ONLY_PATHS or key in OPERATIONAL_PATHS
        loopback = is_loopback_client(request, self._settings) if needs_loopback else False

        if key in LOOPBACK_ONLY_PATHS and not loopback:
            # The schema is a developer surface; a remote client cannot tell it exists.
            return _not_found()
        if key in OPERATIONAL_PATHS and loopback:
            return await call_next(request)
        if not requires_auth(request.method, path, self._settings):
            return await call_next(request)
        # A remote operational request always needs a credential, even when auth is
        # explicitly disabled for the rest of the process.
        if not self._settings.auth_enabled and key not in OPERATIONAL_PATHS:
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
            if (
                expected is None
                and self._settings.environment != "prod"
                and key not in OPERATIONAL_PATHS
            ):
                global _warned_missing_token
                if not _warned_missing_token:
                    _warned_missing_token = True
                    logger.warning(
                        "EZCHAIN_API_TOKEN is unset; allowing protected requests in %s. "
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
    "AUTHENTICATED_READ_PATHS",
    "LOOPBACK_ONLY_PATHS",
    "OPERATIONAL_PATHS",
    "PROTECTED_PREFIX",
    "READ_METHODS",
    "READ_ONLY_POST_PATHS",
    "BearerAuthMiddleware",
    "client_host",
    "is_loopback_client",
    "is_protected_request",
    "required_scope",
    "requires_auth",
]
