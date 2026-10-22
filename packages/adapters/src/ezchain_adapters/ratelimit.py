"""Token-bucket rate limiting and Retry-After parsing.

The bucket sleeps for the exact deficit instead of polling, so a tight loop can never spin
on the event loop. Callers get strict pacing up to ``burst`` immediate requests, while the
long-run rate stays at ``rate_per_second`` (DexScreener: 5/s = 300/min).
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable

DEFAULT_RETRY_AFTER_SECONDS = 1.0
MAX_RETRY_AFTER_SECONDS = 60.0

Sleeper = Callable[[float], Awaitable[None]]


async def default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


class TokenBucket:
    def __init__(
        self,
        *,
        rate_per_second: float,
        burst: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleeper = default_sleep,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        if burst < 1:
            raise ValueError("burst must be at least 1")
        self._rate = float(rate_per_second)
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._updated_at = clock()
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> None:
        if tokens <= 0 or tokens > self._capacity:
            raise ValueError("tokens must be positive and no greater than burst")
        while True:
            async with self._lock:
                now = self._clock()
                elapsed = max(0.0, now - self._updated_at)
                self._updated_at = now
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                wait = (tokens - self._tokens) / self._rate
            await self._sleep(max(wait, 0.001))


def parse_retry_after(
    value: str | None,
    *,
    default: float = DEFAULT_RETRY_AFTER_SECONDS,
    maximum: float = MAX_RETRY_AFTER_SECONDS,
) -> float:
    """Parse a numeric Retry-After header, clamped so a hostile value cannot stall us."""
    if value is None:
        return default
    try:
        seconds = float(value.strip())
    except (TypeError, ValueError):
        return default
    if not math.isfinite(seconds) or seconds < 0:
        return default
    return min(seconds, maximum)
