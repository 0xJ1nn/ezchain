"""HTTP error envelope."""

from __future__ import annotations

from fastapi import HTTPException

from ezchain_adapters import (
    AdapterError,
    CandlesUnsupported,
    NoRoute,
    NotFound,
    RateLimited,
    UpstreamError,
)


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


def candles_http_error(exc: AdapterError) -> HTTPException:
    """Candle-specific reason codes. A chain without a provider is not an upstream fault.

    ``UPSTREAM_UNREACHABLE`` covers a rate limit the adapter exhausted as well as a
    transport failure: both mean "we could not obtain the bars", and the client should
    retry rather than show an empty chart.
    """
    if isinstance(exc, CandlesUnsupported):
        return HTTPException(
            status_code=404,
            detail={"code": exc.code, "message": exc.message, "retryable": False},
        )
    if isinstance(exc, NotFound):
        return HTTPException(
            status_code=404,
            detail={"code": "CANDLES_NOT_FOUND", "message": exc.message, "retryable": False},
        )
    if isinstance(exc, RateLimited | UpstreamError):
        return HTTPException(
            status_code=502,
            detail={
                "code": "UPSTREAM_UNREACHABLE",
                "message": exc.message,
                "retryable": True,
                "detail": exc.detail,
            },
        )
    return adapter_http_error(exc)
