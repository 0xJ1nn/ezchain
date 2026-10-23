"""Adapter errors with stable codes."""

from __future__ import annotations


class AdapterError(Exception):
    code = "ADAPTER_ERROR"
    retryable = False

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class NotFound(AdapterError):
    code = "NOT_FOUND"


class UpstreamError(AdapterError):
    code = "UPSTREAM_ERROR"
    retryable = True


class RateLimited(AdapterError):
    code = "RATE_LIMITED"
    retryable = True


class NoRoute(AdapterError):
    code = "NO_SELL_ROUTE"

    def __init__(self, message: str, *, detail: str | None = None, code: str | None = None) -> None:
        super().__init__(message, detail=detail)
        if code is not None:
            self.code = code


class CandlesUnsupported(AdapterError):
    """This chain or timeframe has no candle provider. Never fabricate history."""

    code = "CANDLES_UNSUPPORTED_CHAIN"
