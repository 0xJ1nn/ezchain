"""HTTP error envelope."""

from __future__ import annotations

from fastapi import HTTPException

from ezchain_adapters import AdapterError, NoRoute, NotFound, RateLimited, UpstreamError


def adapter_http_error(exc: AdapterError) -> HTTPException:
    status = 502
    if isinstance(exc, NotFound):
        status = 404
    elif isinstance(exc, RateLimited):
        status = 429
    elif isinstance(exc, NoRoute):
        status = 409
    elif isinstance(exc, UpstreamError):
        status = 502
    return HTTPException(
        status_code=status,
        detail={"code": exc.code, "message": exc.message, "retryable": exc.retryable},
    )
