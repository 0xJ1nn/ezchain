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


class ProviderKeyUnavailable(AdapterError):
    """A stored provider credential could not be loaded for a provider call.

    The default is permanent: a rotated age identity or a deleted keyring entry fails the
    same way on every attempt, and telling the client to retry only produces a storm for
    an operator-action failure. A store that is briefly unreachable passes
    ``retryable=True`` and keeps the client's retry behaviour.
    """

    code = "PROVIDER_KEY_UNAVAILABLE"
    retryable = False

    def __init__(
        self, message: str, *, detail: str | None = None, retryable: bool = False
    ) -> None:
        super().__init__(message, detail=detail)
        self.retryable = retryable


class NoRoute(AdapterError):
    code = "NO_SELL_ROUTE"

    def __init__(self, message: str, *, detail: str | None = None, code: str | None = None) -> None:
        super().__init__(message, detail=detail)
        if code is not None:
            self.code = code


class CandlesUnsupported(AdapterError):
    """This chain or timeframe has no candle provider. Never fabricate history."""

    code = "CANDLES_UNSUPPORTED_CHAIN"
