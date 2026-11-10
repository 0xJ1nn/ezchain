"""Per-provider circuit breaker.

A public endpoint that keeps failing or timing out should not be retried on every
request: the breaker opens after a configured number of consecutive failures, rejects
calls fast while open, allows a single probe after the cooldown, and closes again on a
success. Only provider-side failures count; a "no data" answer is a fact about the
request, not a broken provider.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from enum import StrEnum

DEFAULT_COOLDOWN_SECONDS = 60.0
DEFAULT_FAILURE_THRESHOLD = 1


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        *,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        threshold: int | None = None,
        max_failures: int | None = None,
        cooldown: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if cooldown is not None:
            cooldown_seconds = cooldown
        if cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds must be positive")
        if threshold is not None:
            failure_threshold = threshold
        if max_failures is not None:
            failure_threshold = max_failures
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        self._cooldown = float(cooldown_seconds)
        self._threshold = int(failure_threshold)
        self._clock = clock
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._open_until = 0.0
        self._probe_in_flight = False
        self._lock = threading.Lock()

    def _maybe_transition(self) -> None:
        if self._state == BreakerState.OPEN and self._clock() >= self._open_until:
            self._state = BreakerState.HALF_OPEN
            self._probe_in_flight = False

    def is_open(self) -> bool:
        """Whether a call should be rejected right now.

        While open and inside the cooldown this is ``True``. Once the cooldown elapses
        the breaker moves to half-open and allows exactly one probe, so this returns
        ``False`` for that probe only.
        """
        with self._lock:
            self._maybe_transition()
            if self._state == BreakerState.OPEN:
                return True
            if self._state == BreakerState.HALF_OPEN:
                if self._probe_in_flight:
                    return True
                self._probe_in_flight = True
                return False
            return False

    def record_failure(self) -> None:
        with self._lock:
            self._maybe_transition()
            self._consecutive_failures += 1
            if self._state == BreakerState.HALF_OPEN:
                self._open_until = self._clock() + self._cooldown
                self._state = BreakerState.OPEN
                self._probe_in_flight = False
                return
            if self._consecutive_failures >= self._threshold:
                self._open_until = self._clock() + self._cooldown
                self._state = BreakerState.OPEN
                self._probe_in_flight = False

    def record_success(self) -> None:
        with self._lock:
            self._maybe_transition()
            self._consecutive_failures = 0
            self._state = BreakerState.CLOSED
            self._open_until = 0.0
            self._probe_in_flight = False

    def reset_probe(self) -> None:
        with self._lock:
            self._probe_in_flight = False

    @property
    def state(self) -> str:
        self._maybe_transition()
        return self._state.value

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def open_until(self) -> float:
        return self._open_until

    # Backwards-compatible aliases for the previous single-failure breaker.
    def record(self, success: bool) -> None:  # pragma: no cover - compatibility shim
        if success:
            self.record_success()
        else:
            self.record_failure()
