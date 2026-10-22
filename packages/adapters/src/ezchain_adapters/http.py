"""Shared JSON-over-HTTP plumbing for public market-data adapters.

One bounded cache, one token bucket, one in-flight request table and one 429 cooldown per
client. Concurrent callers asking for the same URL share a single request, successes are
cached, and a rate-limit response sets a cooldown instead of retrying in a storm.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar, Self

import httpx

from .cache import MISSING, BoundedTTLCache
from .errors import NotFound, RateLimited, UpstreamError
from .ratelimit import TokenBucket, default_sleep, parse_retry_after

DEFAULT_TIMEOUT_SECONDS = 15.0
USER_AGENT = "ezCHAIN/0.1 (+https://github.com/0xJ1nn/ezchain)"

Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]


def _retrieve_exception(future: asyncio.Future[Any]) -> None:
    """Mark a shared future's exception as observed when nobody else awaited it."""
    if not future.cancelled():
        future.exception()


class JsonApiClient:
    """Base class for read-only JSON APIs with cache, pacing and request dedupe."""

    label: ClassVar[str]
    base_url: ClassVar[str]
    cache_ttl_seconds: ClassVar[float]
    rate_per_second: ClassVar[float]
    burst: ClassVar[int]
    cache_max_entries: ClassVar[int] = 256
    not_found_statuses: ClassVar[frozenset[int]] = frozenset()

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        limiter: TokenBucket | None = None,
        cache: BoundedTTLCache | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleeper = default_sleep,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout
        self._clock = clock
        self._limiter = limiter or TokenBucket(
            rate_per_second=self.rate_per_second,
            burst=self.burst,
            clock=clock,
            sleep=sleep,
        )
        self._cache = cache or BoundedTTLCache(
            max_entries=self.cache_max_entries,
            ttl_seconds=self.cache_ttl_seconds,
            clock=clock,
        )
        self._inflight: dict[str, asyncio.Future[Any]] = {}
        self._cooldown_until = 0.0

    async def __aenter__(self) -> Self:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self._timeout),
                headers={"User-Agent": USER_AGENT},
            )
            self._owns_client = True
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        key = f"{path}?{sorted((params or {}).items())}"
        cached = self._cache.get(key)
        if cached is not MISSING:
            return cached
        inflight = self._inflight.get(key)
        if inflight is not None:
            return await asyncio.shield(inflight)

        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        future.add_done_callback(_retrieve_exception)
        self._inflight[key] = future
        try:
            payload = await self._request(path, params)
        except asyncio.CancelledError:
            future.cancel()
            raise
        except Exception as exc:
            future.set_exception(exc)
            raise
        else:
            future.set_result(payload)
            self._cache.set(key, payload)
            return payload
        finally:
            self._inflight.pop(key, None)

    async def _request(self, path: str, params: dict[str, Any] | None) -> Any:
        if self._client is None:
            raise UpstreamError(f"{self.label} client is not open")
        if self._clock() < self._cooldown_until:
            raise RateLimited(f"{self.label} rate limit", detail="cooldown active")
        await self._limiter.acquire()
        try:
            response = await self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise UpstreamError(f"{self.label} request failed: {exc}") from exc
        if response.status_code == 429:
            retry_after = parse_retry_after(response.headers.get("Retry-After"))
            self._cooldown_until = max(self._cooldown_until, self._clock() + retry_after)
            raise RateLimited(
                f"{self.label} rate limit", detail=f"retry_after={retry_after:g}s"
            )
        if response.status_code in self.not_found_statuses:
            raise NotFound(f"{self.label} has no route")
        if response.status_code >= 400:
            raise UpstreamError(f"{self.label} returned {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamError(f"{self.label} returned malformed JSON") from exc
