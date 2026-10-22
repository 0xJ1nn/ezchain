"""Bounded TTL cache for adapter responses.

The cache exists to keep us inside a public API's request budget, not to hide staleness.
It therefore has a hard entry limit, expires on read and evicts the least recently used
entry when full. Every entry is partitioned by its request key, so a cached response can
never be served for a different chain, token or query.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Final


class _Missing:
    """Sentinel type: a cached ``None`` must stay distinguishable from a miss."""

    __slots__ = ()


MISSING: Final = _Missing()


class BoundedTTLCache:
    def __init__(
        self,
        *,
        max_entries: int,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._items: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any:
        entry = self._items.get(key)
        if entry is None:
            return MISSING
        expires_at, value = entry
        if expires_at <= self._clock():
            del self._items[key]
            return MISSING
        self._items.move_to_end(key)
        return value

    def set(self, key: str, value: Any) -> None:
        now = self._clock()
        self._purge_expired(now)
        self._items[key] = (now + self._ttl_seconds, value)
        self._items.move_to_end(key)
        while len(self._items) > self._max_entries:
            self._items.popitem(last=False)

    def clear(self) -> None:
        self._items.clear()

    def _purge_expired(self, now: float) -> None:
        expired = [key for key, (expires_at, _) in self._items.items() if expires_at <= now]
        for key in expired:
            del self._items[key]

    def __len__(self) -> int:
        return len(self._items)
