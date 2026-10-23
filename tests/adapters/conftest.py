"""Shared fixtures for adapter contract tests.

Every test that exercises an adapter runs under ``respx``; no test touches a live API.
Fixtures under ``fixtures/`` are trimmed recordings of real responses captured on
2026-10-22 (see the module docstrings in the recording script's output).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from ezchain_adapters import (
    DecimalsResolver,
    DexScreenerClient,
    GeckoTerminalClient,
    JupiterClient,
    QuoteRequest,
)
from ezchain_adapters.ratelimit import TokenBucket
from ezchain_adapters.registry import get_chain

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class FakeClock:
    """Deterministic monotonic clock; sleeping advances time instead of blocking."""

    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def load_fixture() -> Callable[[str], Any]:
    def load(name: str) -> Any:
        return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))

    return load


@pytest.fixture
def fast_limiter(clock: FakeClock) -> TokenBucket:
    return TokenBucket(rate_per_second=10_000.0, burst=10_000, clock=clock, sleep=clock.sleep)


@pytest.fixture
def dexscreener(clock: FakeClock, fast_limiter: TokenBucket) -> DexScreenerClient:
    return DexScreenerClient(limiter=fast_limiter, clock=clock, sleep=clock.sleep)


@pytest.fixture
def jupiter(clock: FakeClock, fast_limiter: TokenBucket) -> JupiterClient:
    return JupiterClient(limiter=fast_limiter, clock=clock, sleep=clock.sleep)


@pytest.fixture
def geckoterminal(clock: FakeClock, fast_limiter: TokenBucket) -> GeckoTerminalClient:
    return GeckoTerminalClient(limiter=fast_limiter, clock=clock, sleep=clock.sleep)


@pytest.fixture
def decimals() -> DecimalsResolver:
    """A resolver with no metadata client: tests seed the decimals they need."""
    return DecimalsResolver()


def quote_request(
    *,
    chain: str,
    token: str,
    side: str = "buy",
    amount_usd: str = "100",
    quantity: str | None = None,
    slippage_bps: int = 50,
) -> QuoteRequest:
    meta = get_chain(chain)
    assert meta is not None, chain
    return QuoteRequest(
        chain=meta,
        token_address=token,
        side="buy" if side == "buy" else "sell",  # type: ignore[arg-type]
        amount_usd=Decimal(amount_usd),
        quantity=None if quantity is None else Decimal(quantity),
        slippage_bps=slippage_bps,
    )
