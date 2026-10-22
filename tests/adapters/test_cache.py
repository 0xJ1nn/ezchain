"""Bounded cache behaviour: TTL, LRU eviction and config validation."""

from __future__ import annotations

import pytest
from conftest import FakeClock

from ezchain_adapters.cache import MISSING, BoundedTTLCache


def test_cached_value_round_trips_including_none(clock: FakeClock) -> None:
    cache = BoundedTTLCache(max_entries=2, ttl_seconds=10, clock=clock)
    cache.set("value", 42)
    cache.set("none", None)
    assert cache.get("value") == 42
    assert cache.get("none") is None
    assert cache.get("missing") is MISSING


def test_entry_expires_on_read(clock: FakeClock) -> None:
    cache = BoundedTTLCache(max_entries=2, ttl_seconds=10, clock=clock)
    cache.set("a", 1)
    clock.advance(9.9)
    assert cache.get("a") == 1
    clock.advance(0.1)
    assert cache.get("a") is MISSING


def test_full_cache_evicts_least_recently_used(clock: FakeClock) -> None:
    cache = BoundedTTLCache(max_entries=2, ttl_seconds=10, clock=clock)
    cache.set("a", 1)
    cache.set("b", 2)
    assert cache.get("a") == 1  # refresh a
    cache.set("c", 3)
    assert cache.get("b") is MISSING
    assert cache.get("a") == 1
    assert cache.get("c") == 3
    assert len(cache) == 2


def test_expired_entries_are_purged_before_eviction(clock: FakeClock) -> None:
    cache = BoundedTTLCache(max_entries=2, ttl_seconds=10, clock=clock)
    cache.set("a", 1)
    cache.set("b", 2)
    clock.advance(11)
    cache.set("c", 3)
    cache.set("d", 4)
    assert len(cache) == 2
    assert cache.get("a") is MISSING
    assert cache.get("b") is MISSING
    assert cache.get("c") == 3
    assert cache.get("d") == 4


def test_clear_removes_everything(clock: FakeClock) -> None:
    cache = BoundedTTLCache(max_entries=2, ttl_seconds=10, clock=clock)
    cache.set("a", 1)
    cache.clear()
    assert len(cache) == 0
    assert cache.get("a") is MISSING


@pytest.mark.parametrize(
    ("max_entries", "ttl_seconds"),
    [(0, 10), (-1, 10), (1, 0), (1, -0.1)],
)
def test_invalid_configuration_is_rejected(
    clock: FakeClock, max_entries: int, ttl_seconds: float
) -> None:
    with pytest.raises(ValueError):
        BoundedTTLCache(max_entries=max_entries, ttl_seconds=ttl_seconds, clock=clock)
