"""Bounded, per-provider request budgets for provider credentials.

Jupiter documents one request per second for a keyed account. The manager hands a caller
a slot through the existing token bucket, but keeps a bounded queue in front of it: a
burst of callers waits for the budget, and each caller's total wait is capped, so one
that cannot be served inside that cap is refused with :class:`ProviderBudgetExhausted`
rather than overrunning the timeout its request is under. The API renders that exception
as a typed 429 instead of holding the request open, or letting it fail as a generic
timeout.

The budget is acquired per provider HTTP request, but the timeout belongs to the route
attempt that contains it (one quote can make several requests). :func:`attempt_deadline_scope`
publishes that attempt's absolute deadline for its duration, so a wait that could only
finish after the attempt is refused as the typed 429 rather than cancelled by the outer
``asyncio.wait_for`` and reported as a route timeout.

The manager is deliberately independent of transport. A provider call is any awaitable
``operation``; the budget is acquired before it starts.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
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
    # The attempt deadline below shortens this further when the attempt has less time.
    max_queue=4,
    max_wait_seconds=2.0,
)
DEFAULT_LIMITS: dict[str, ProviderLimit] = {JUPITER_LIMIT.provider: JUPITER_LIMIT}


@dataclass(frozen=True)
class AttemptDeadline:
    """The absolute end of the provider attempt that owns the current call.

    ``at`` is on ``clock``'s timeline, not the budget's, because the attempt measures its
    own remaining time. Pairing the value with its clock keeps the two from being mixed
    when a caller injects a different one.
    """

    at: float
    clock: Clock = time.monotonic

    def remaining(self) -> float:
        return self.at - self.clock()


_ATTEMPT_DEADLINE: ContextVar[AttemptDeadline | None] = ContextVar(
    "ezchain_provider_attempt_deadline", default=None
)


@contextmanager
def attempt_deadline_scope(seconds: float, *, clock: Clock = time.monotonic) -> Iterator[None]:
    """Publish the owning attempt's deadline for the provider calls made inside it.

    Only the router knows how much of an attempt is left. Without this, each acquisition
    measures its wait from the moment it joins the queue, and the second request of a
    slow attempt can sleep past the attempt's ``wait_for``; the caller then sees a route
    timeout (409) instead of the typed budget refusal (429) that is the truth.
    """
    token = _ATTEMPT_DEADLINE.set(AttemptDeadline(at=clock() + seconds, clock=clock))
    try:
        yield
    finally:
        _ATTEMPT_DEADLINE.reset(token)


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

    @property
    def waiting(self) -> int:
        """Callers queued for a slot right now."""
        return self._waiting

    def _deadline(self) -> float:
        """The last clock value this acquisition may wait until.

        The limit's window is measured from now, but the attempt that contains this
        request may have less time: it owns the timeout the caller is under, and a wait
        that could only finish after it must be refused now, while this can still be
        reported as a typed 429 instead of being cancelled by the outer ``wait_for``.
        """
        now = self._clock()
        wait_cap = self.limit.max_wait_seconds
        attempt = _ATTEMPT_DEADLINE.get()
        if attempt is not None:
            remaining = attempt.remaining()
            if remaining <= 0:
                raise ProviderBudgetExhausted(
                    self.limit.provider, detail="the attempt deadline has passed"
                )
            wait_cap = min(wait_cap, remaining)
        return now + wait_cap

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
        try:
            await self._bucket.acquire(deadline=self._deadline())
        except ProviderBudgetExhausted:
            raise
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

    def waiting(self, provider: str) -> int:
        """Callers currently queued for one provider's budget.

        This is the queue depth the ops surface reports: a caller that entered
        :meth:`_Budget.acquire` and has not yet been served or refused. Unknown providers
        are zero, not an invented series.
        """
        budget = self._limits.get(provider)
        return 0 if budget is None else budget.waiting

    def depths(self) -> dict[str, int]:
        """Queued callers per configured provider, for the ops snapshot."""
        return {provider: budget.waiting for provider, budget in self._limits.items()}

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
    "AttemptDeadline",
    "DEFAULT_LIMITS",
    "JUPITER_LIMIT",
    "ProviderBudgetExhausted",
    "ProviderBudgetManager",
    "ProviderLimit",
    "attempt_deadline_scope",
]
