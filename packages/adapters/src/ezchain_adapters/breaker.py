"""Per-provider circuit breaker for the quote adapters.

A public quote API that answers 5xx, a Cloudflare 530 or an HTML interstitial keeps doing
so for a while. Without a breaker every order pays that timeout again; with one the ladder
moves to the next provider immediately and the failing provider is retried after the
cooldown. Only provider-side failures open the circuit: a "no route for this token" answer
is a fact about the token, not a broken provider, and a 429 already has its own cooldown.
"""

from __future__ import annotations

import time
from collections.abc import Callable

DEFAULT_COOLDOWN_SECONDS = 60.0


class CircuitBreaker:
    def __init__(
        self,
        *,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds must be positive")
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._open_until = 0.0

    def is_open(self) -> bool:
        return self._clock() < self._open_until

    def record_failure(self) -> None:
        self._open_until = max(self._open_until, self._clock() + self._cooldown)

    def record_success(self) -> None:
        self._open_until = 0.0

    @property
    def open_until(self) -> float:
        """Monotonic instant the circuit closes again (for logs and tests)."""
        return self._open_until
