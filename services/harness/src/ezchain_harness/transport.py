"""One HTTP call, as a value a test can fake.

A client hands the transport a :class:`HttpRequest` and reads back an
:class:`HttpResponse`; it never opens a socket itself. The suite therefore exercises the
whole mapping — message shapes, tool-call round-trips, usage and errors — with no network,
no key and no provider account. :class:`HttpxTransport` is the only thing in the package
that touches the network, and it is a dozen lines.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .errors import ProviderTimeout, ProviderTransportError


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: Mapping[str, str]
    json: Mapping[str, Any]
    timeout: float


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: Mapping[str, Any]


class Transport(Protocol):
    def __call__(self, request: HttpRequest) -> HttpResponse: ...


class HttpxTransport:
    """The real transport: one call, no retries, no redirects to a second host."""

    def __call__(self, request: HttpRequest) -> HttpResponse:
        try:
            response = httpx.request(
                request.method,
                request.url,
                headers=dict(request.headers),
                json=dict(request.json),
                timeout=request.timeout,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(
                f"{request.url} did not answer within {request.timeout:g}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderTransportError(f"{request.url}: {type(exc).__name__}") from exc
        body: Any = {}
        try:
            body = response.json()
        except ValueError:
            body = {}
        return HttpResponse(
            status=response.status_code,
            body=body if isinstance(body, dict) else {},
        )


__all__ = ["HttpRequest", "HttpResponse", "HttpxTransport", "Transport"]
