"""A small fixed-window read rate limiter keyed by peer or principal."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from .config import Settings, get_settings

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_MAX_KEYS = 10_000


@dataclass
class _Bucket:
    count: int = 0
    window_start: float = 0.0
    last_access: float = 0.0


class ReadRateLimiter:
    def __init__(self, settings: Settings, *, max_keys: int = _MAX_KEYS) -> None:
        self._settings = settings
        self._max_keys = max_keys
        self._buckets: dict[str, _Bucket] = {}

    @property
    def settings(self) -> Settings:
        return self._settings

    def allow(self, key: str) -> tuple[bool, int]:
        limit = self._settings.read_rate_limit_per_minute
        if limit <= 0:
            return True, 0
        now = time.monotonic()
        window = 60.0
        bucket = self._buckets.get(key)
        if bucket is None or now - bucket.window_start >= window:
            if bucket is None and len(self._buckets) >= self._max_keys:
                oldest_key = min(self._buckets.items(), key=lambda item: item[1].last_access)[0]
                del self._buckets[oldest_key]
            bucket = _Bucket(count=0, window_start=now, last_access=now)
            self._buckets[key] = bucket
        bucket.last_access = now
        if bucket.count >= limit:
            retry_after = max(1, int(window - (now - bucket.window_start) + 0.999))
            return False, retry_after
        bucket.count += 1
        return True, 0


class ReadRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        settings: Settings | None = None,
        limiter: ReadRateLimiter | None = None,
        session_factory: Any = None,
    ) -> None:
        super().__init__(app)
        self._settings = settings if settings is not None else get_settings()
        self._limiter = limiter if limiter is not None else ReadRateLimiter(self._settings)

    @property
    def limiter(self) -> ReadRateLimiter:
        return self._limiter

    async def dispatch(self, request: Any, call_next: RequestResponseEndpoint) -> Any:
        settings = self._settings
        path = request.url.path
        if request.method.upper() not in READ_METHODS:
            return await call_next(request)
        if not path.startswith("/api/"):
            return await call_next(request)
        if settings.read_rate_limit_per_minute <= 0:
            return await call_next(request)

        principal = getattr(getattr(request, "state", None), "principal", None)
        if principal is not None:
            key = "principal:" + str(getattr(principal, "subject", principal))
        else:
            from .auth import client_host

            key = "peer:" + str(client_host(request, settings))

        allowed, retry_after = self._limiter.allow(key)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": {
                        "code": "RATE_LIMITED",
                        "message": "too many requests",
                        "retryable": True,
                    }
                },
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)


RateLimitMiddleware = ReadRateLimitMiddleware
RateLimiterMiddleware = ReadRateLimitMiddleware
LimiterMiddleware = ReadRateLimitMiddleware
