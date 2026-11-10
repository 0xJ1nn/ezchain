"""Provider request budgets: wait for a slot, refuse a full queue, type the refusal."""

from __future__ import annotations

import asyncio

import pytest

from ezchain_adapters import (
    JUPITER_LIMIT,
    ProviderBudgetExhausted,
    ProviderBudgetManager,
    ProviderLimit,
)
from ezchain_api.errors import adapter_http_error, provider_budget_http_error


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class BlockingClock(FakeClock):
    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()
        self.entered = asyncio.Event()

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.entered.set()
        await self.release.wait()
        self.now += seconds


async def immediate(value: str) -> str:
    return value


def test_jupiter_is_limited_to_one_request_per_second() -> None:
    assert JUPITER_LIMIT.requests_per_second == 1.0
    assert JUPITER_LIMIT.burst == 1
    # The quote router gives a provider 5 s per attempt. A budget wait must end inside
    # that window so exhaustion is a typed 429, not a generic timeout/NO_ROUTE.
    assert JUPITER_LIMIT.max_wait_seconds < 5.0


async def test_a_provider_call_waits_for_the_budget() -> None:
    clock = FakeClock()
    manager = ProviderBudgetManager(clock=clock, sleep=clock.sleep)

    assert await manager.call("jupiter", lambda: immediate("first")) == "first"
    assert clock.sleeps == []

    assert await manager.call("jupiter", lambda: immediate("second")) == "second"
    assert clock.sleeps == [1.0]


async def test_exhaustion_is_typed_and_maps_to_a_429() -> None:
    clock = FakeClock()
    manager = ProviderBudgetManager(
        limits=[ProviderLimit("jupiter", 1.0, 1, 8, 0.25)],
        clock=clock,
        sleep=clock.sleep,
    )
    await manager.call("jupiter", lambda: immediate("first"))

    with pytest.raises(ProviderBudgetExhausted) as caught:
        await manager.call("jupiter", lambda: immediate("second"))

    assert caught.value.code == "PROVIDER_BUDGET_EXHAUSTED"
    assert caught.value.http_status == 429
    error = provider_budget_http_error(caught.value)
    assert error.status_code == 429
    assert error.detail["code"] == "PROVIDER_BUDGET_EXHAUSTED"
    assert error.detail["provider"] == "jupiter"
    assert error.headers == {"Retry-After": "1"}
    mapped = adapter_http_error(caught.value)
    assert mapped.status_code == 429
    assert mapped.detail["code"] == "PROVIDER_BUDGET_EXHAUSTED"
    assert mapped.headers == {"Retry-After": "1"}


async def test_a_full_queue_is_refused_immediately() -> None:
    clock = BlockingClock()
    manager = ProviderBudgetManager(
        limits=[ProviderLimit("jupiter", 1.0, 1, 1, 60.0)],
        clock=clock,
        sleep=clock.sleep,
    )
    gate = asyncio.Event()
    started = asyncio.Event()

    async def hold() -> str:
        started.set()
        await gate.wait()
        return "first"

    first = asyncio.create_task(manager.call("jupiter", hold))
    await started.wait()
    second = asyncio.create_task(manager.call("jupiter", lambda: immediate("second")))
    await clock.entered.wait()

    with pytest.raises(ProviderBudgetExhausted) as caught:
        await manager.call("jupiter", lambda: immediate("third"))
    assert caught.value.detail == "queue is full"

    gate.set()
    clock.release.set()
    assert await first == "first"
    assert await second == "second"


def test_an_unknown_provider_has_no_budget() -> None:
    manager = ProviderBudgetManager(clock=FakeClock(), sleep=FakeClock().sleep)
    with pytest.raises(ValueError):
        manager.limit("unknown")


def test_a_limit_must_be_usable() -> None:
    with pytest.raises(ValueError):
        ProviderLimit("jupiter", 0.0, 1, 1, 1.0)
    with pytest.raises(ValueError):
        ProviderLimit("jupiter", 1.0, 1, 0, 1.0)
    with pytest.raises(ValueError):
        ProviderLimit("jupiter", 1.0, 1, 1, 0.0)
