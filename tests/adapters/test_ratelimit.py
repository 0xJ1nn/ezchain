"""Token bucket pacing and Retry-After parsing."""

from __future__ import annotations

import pytest

from ezchain_adapters.ratelimit import (
    MAX_RETRY_AFTER_SECONDS,
    TokenBucket,
    parse_retry_after,
)

from .conftest import FakeClock


async def test_burst_is_immediate_then_one_exact_sleep(clock: FakeClock) -> None:
    bucket = TokenBucket(rate_per_second=2.0, burst=2, clock=clock, sleep=clock.sleep)
    await bucket.acquire()
    await bucket.acquire()
    assert clock.sleeps == []

    await bucket.acquire()
    assert clock.sleeps == [0.5]
    assert clock.now == 1_000.5


async def test_refill_uses_elapsed_time(clock: FakeClock) -> None:
    bucket = TokenBucket(rate_per_second=2.0, burst=2, clock=clock, sleep=clock.sleep)
    await bucket.acquire()
    await bucket.acquire()
    clock.advance(0.6)  # 1.2 tokens available
    await bucket.acquire()  # no sleep needed
    assert clock.sleeps == []


async def test_bucket_never_exceeds_burst_after_idle(clock: FakeClock) -> None:
    bucket = TokenBucket(rate_per_second=1.0, burst=3, clock=clock, sleep=clock.sleep)
    clock.advance(1_000)
    for _ in range(3):
        await bucket.acquire()
    assert clock.sleeps == []
    await bucket.acquire()
    assert clock.sleeps == [1.0]


@pytest.mark.parametrize(("rate", "burst"), [(0, 1), (-1, 1), (1, 0)])
def test_invalid_bucket_configuration_is_rejected(
    clock: FakeClock, rate: float, burst: int
) -> None:
    with pytest.raises(ValueError):
        TokenBucket(rate_per_second=rate, burst=burst, clock=clock, sleep=clock.sleep)


async def test_acquire_more_than_burst_is_rejected(clock: FakeClock) -> None:
    bucket = TokenBucket(rate_per_second=1.0, burst=2, clock=clock, sleep=clock.sleep)
    with pytest.raises(ValueError):
        await bucket.acquire(3)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, 1.0),
        ("12", 12.0),
        ("1.5", 1.5),
        (" 2 ", 2.0),
        ("0", 0.0),
        ("-3", 1.0),
        ("nan", 1.0),
        ("inf", 1.0),
        ("soon", 1.0),
        ("9999", MAX_RETRY_AFTER_SECONDS),
    ],
)
def test_retry_after_is_parsed_and_clamped(header: str | None, expected: float) -> None:
    assert parse_retry_after(header) == expected
