"""Bounded, per-provider request budgets for provider credentials.

Jupiter documents one request per second for a keyed account. The manager hands a caller
a slot through the existing token bucket, but keeps a bounded queue in front of it: a
burst of callers waits for the budget, and each caller's total wait is capped, so one
that cannot be served inside that cap is refused with :class:`ProviderBudgetExhausted`
rather than overrunning the timeout its request is under. The API renders that exception
as a typed 429 instead of holding the request open, or letting it fail as a generic
timeout.

The manager is deliberately independent of transport. A provider call is any awaitable
``operation``; the budget is acquired before it starts.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import TypeVar

from .errors import RateLimited
from .ratelimit import Sleeper, TokenBucket, default_sleep

T = TypeVar("T")
Clock = Callable[[], float]


@dataclass(frozen=True)
class ProviderLimit:
    provider: str
    requests_per_second: float
    burst: int
    max_queue: int
    max_wait_seconds: float

    def __post_init__(self) -> None:
        if not self.provider:
            raise ValueError("provider must not be empty")
        if self.requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        if self.burst < 1:
            raise ValueError("burst must be at least 1")
        if self.max_queue < 1:
            raise ValueError("max_queue must be at least 1")
        if self.max_wait_seconds <= 0:
            raise ValueError("max_wait_seconds must be positive")


JUPITER_LIMIT = ProviderLimit(
    provider="jupiter",
    requests_per_second=1.0,
    burst=1,
    # The wait budget is the total a caller may spend queued, and it stays below the
    # quote router's per-provider attempt timeout (5 s) so a slow queue raises the typed
    # budget error before the outer wait_for turns it into a generic timeout/NO_ROUTE.
    # Queue depth is not the bound: four callers waiting 1 s each would still be 4 s.
    max_queue=4,
    max_wait_seconds=2.0,
)
DEFAULT_LIMITS: dict[str, ProviderLimit] = {JUPITER_LIMIT.provider: JUPITER_LIMIT}


class ProviderBudgetExhausted(RateLimited):
    """The provider's queue or wait budget is exhausted. Typed as HTTP 429."""

    code = "PROVIDER_BUDGET_EXHAUSTED"
    http_status = 429

    def __init__(self, provider: str, *, detail: str) -> None:
        super().__init__(f"{provider} request budget exhausted", detail=detail)
        self.provider = provider


class _Budget:
    def __init__(self, limit: ProviderLimit, *, clock: Clock, sleep: Sleeper) -> None:
        self.limit = limit
        self._clock = clock
        self._bucket = TokenBucket(
            rate_per_second=limit.requests_per_second,
            burst=limit.burst,
            clock=clock,
            sleep=sleep,
            max_wait_seconds=limit.max_wait_seconds,
        )
        self._waiting = 0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            if self._waiting >= self.limit.max_queue:
                raise ProviderBudgetExhausted(self.limit.provider, detail="queue is full")
            self._waiting += 1
        # Measured from the moment this caller joins the queue. A caller behind several
        # competitors can lose the slot once per competitor, so the wait it deserves is
        # the accumulated one; bounding a single sleep instead lets it run past the
        # caller's attempt timeout, which the route can only report as a generic
        # timeout/NO_ROUTE rather than the typed 429 this budget promises.
        deadline = self._clock() + self.limit.max_wait_seconds
        try:
            await self._bucket.acquire(deadline=deadline)
        except RateLimited as exc:
            raise ProviderBudgetExhausted(
                self.limit.provider,
                detail=exc.detail or "next slot is beyond the wait window",
            ) from exc
        finally:
            async with self._lock:
                self._waiting -= 1


class ProviderBudgetManager:
    """One bounded budget per provider, shared by every caller in the process."""

    def __init__(
        self,
        limits: Iterable[ProviderLimit] | None = None,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleeper = default_sleep,
    ) -> None:
        configured = DEFAULT_LIMITS.values() if limits is None else limits
        self._limits = {
            limit.provider: _Budget(limit, clock=clock, sleep=sleep) for limit in configured
        }

    def limit(self, provider: str) -> ProviderLimit:
        budget = self._limits.get(provider)
        if budget is None:
            raise ValueError(f"no request budget is configured for {provider!r}")
        return budget.limit

    async def acquire(self, provider: str) -> None:
        """Wait for a slot, or raise :class:`ProviderBudgetExhausted`."""
        budget = self._limits.get(provider)
        if budget is None:
            raise ValueError(f"no request budget is configured for {provider!r}")
        await budget.acquire()

    async def call(self, provider: str, operation: Callable[[], Awaitable[T]]) -> T:
        """Run ``operation`` under the provider's budget."""
        await self.acquire(provider)
        return await operation()


__all__ = [
    "DEFAULT_LIMITS",
    "JUPITER_LIMIT",
    "ProviderBudgetExhausted",
    "ProviderBudgetManager",
    "ProviderLimit",
]
