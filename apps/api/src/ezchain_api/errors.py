"""HTTP error envelope."""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ezchain_adapters import (
    AdapterError,
    CandlesUnsupported,
    NoRoute,
    NotFound,
    ProviderBudgetExhausted,
    ProviderKeyUnavailable,
    RateLimited,
    UpstreamError,
)


def provider_budget_http_error(exc: ProviderBudgetExhausted) -> HTTPException:
    """A bounded provider queue refused the call. Typed 429, retry later."""
    return HTTPException(
        status_code=429,
        detail={
            "code": exc.code,
            "message": exc.message,
            "retryable": True,
            "provider": exc.provider,
        },
        headers={"Retry-After": "1"},
    )


def adapter_http_error(exc: AdapterError) -> HTTPException:
    if isinstance(exc, ProviderBudgetExhausted):
        return provider_budget_http_error(exc)
    status = 502
    if isinstance(exc, NotFound):
        status = 404
    elif isinstance(exc, RateLimited):
        status = 429
    elif isinstance(exc, NoRoute):
        status = 409
    elif isinstance(exc, ProviderKeyUnavailable):
        status = 503
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


def json_safe(value: Any) -> Any:
    """Make validation errors serializable even when the rejected input was inf/NaN."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, Decimal) and not value.is_finite():
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def refuse_unknown_query(request: Request, allowed: frozenset[str]) -> None:
    """Refuse query parameters a route does not declare, naming each in ``loc``.

    FastAPI silently ignores a query parameter it has no argument for, so a misspelt
    field would otherwise be accepted with the default in force. This closes a query
    surface the same way ``extra="forbid"`` closes a request body, and reuses the shared
    validation handler so the body shape stays the standard ``422`` list.
    """
    unknown = [name for name in dict.fromkeys(request.query_params) if name not in allowed]
    if not unknown:
        return
    raise RequestValidationError(
        [
            {
                "type": "extra_forbidden",
                "loc": ("query", name),
                "msg": "Extra inputs are not permitted",
                "input": request.query_params.get(name),
            }
            for name in unknown
        ]
    )


_BODY_SECRET_PATHS = frozenset({"/api/v1/provider-keys"})


def _body_may_contain_a_secret(request: Request) -> bool:
    return request.url.path.rstrip("/") in _BODY_SECRET_PATHS


async def request_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Never turn a rejected non-finite JSON number into a 500 while rendering it.

    Pydantic echoes the rejected body under ``input``. On a route that accepts a secret,
    that echo is the plaintext key. Those fields are dropped before the response is built.
    """
    errors = exc.errors()
    if _body_may_contain_a_secret(request):
        errors = [
            {key: value for key, value in error.items() if key not in {"input", "ctx"}}
            for error in errors
        ]
    return JSONResponse(status_code=422, content={"detail": json_safe(errors)})
