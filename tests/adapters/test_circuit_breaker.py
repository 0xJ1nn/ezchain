"""Circuit breaker and transport failure classification.

A 5xx, a Cloudflare 530 or an HTML interstitial is a provider outage: it must open the
circuit for 60 s so the ladder moves on instead of paying the same timeout on every order.
A "no route" answer or a 429 must not: those are facts about one token, not a dead host.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx

from ezchain_adapters import (
    CircuitBreaker,
    DecimalsResolver,
    KyberSwapClient,
    NotFound,
    UpstreamError,
)
from ezchain_adapters.ratelimit import TokenBucket

from .conftest import FakeClock, quote_request

BASE_ROUTES = "https://aggregator-api.kyberswap.com/base/api/v1/routes"
WETH = "0x4200000000000000000000000000000000000006"


def test_breaker_opens_for_the_cooldown_and_closes(clock: FakeClock) -> None:
    breaker = CircuitBreaker(cooldown_seconds=60.0, clock=clock)
    assert breaker.is_open() is False
    breaker.record_failure()
    assert breaker.is_open() is True
    clock.advance(59)
    assert breaker.is_open() is True
    clock.advance(2)
    assert breaker.is_open() is False


def test_success_closes_the_breaker(clock: FakeClock) -> None:
    breaker = CircuitBreaker(cooldown_seconds=60.0, clock=clock)
    breaker.record_failure()
    breaker.record_success()
    assert breaker.is_open() is False


def test_cooldown_must_be_positive(clock: FakeClock) -> None:
    with pytest.raises(ValueError):
        CircuitBreaker(cooldown_seconds=0.0, clock=clock)


@pytest.fixture
def kyber(clock: FakeClock, fast_limiter: TokenBucket, decimals: DecimalsResolver) -> Any:
    decimals.remember("base", WETH, 18)
    return KyberSwapClient(decimals=decimals, limiter=fast_limiter, clock=clock, sleep=clock.sleep)


@respx.mock
async def test_html_interstitial_opens_the_circuit(
    kyber: KyberSwapClient, load_fixture: Callable[[str], Any]
) -> None:
    route = respx.get(BASE_ROUTES).mock(
        return_value=httpx.Response(
            200,
            text="<html><body>Just a moment…</body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )
    async with kyber as client:
        with pytest.raises(UpstreamError):
            await client.quote(quote_request(chain="base", token=WETH))
        with pytest.raises(UpstreamError):
            await client.quote(quote_request(chain="base", token=WETH))
    assert route.call_count == 1


@respx.mock
async def test_no_route_answer_does_not_open_the_circuit(kyber: KyberSwapClient) -> None:
    route = respx.get(BASE_ROUTES).mock(
        return_value=httpx.Response(200, json={"code": 4011, "message": "token not found"})
    )
    async with kyber as client:
        # different sizes so the 10 s response cache cannot answer the second call
        for amount in ("100", "101"):
            with pytest.raises(NotFound):
                await client.quote(quote_request(chain="base", token=WETH, amount_usd=amount))
    # both calls reached the provider: a missing pool is not an outage
    assert route.call_count == 2


@respx.mock
async def test_429_sets_a_cooldown_without_opening_the_breaker(kyber: KyberSwapClient) -> None:
    route = respx.get(BASE_ROUTES).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "2"})
    )
    async with kyber as client:
        with pytest.raises(Exception) as first:
            await client.quote(quote_request(chain="base", token=WETH))
        assert first.value.__class__.__name__ == "RateLimited"
        with pytest.raises(Exception) as second:
            await client.quote(quote_request(chain="base", token=WETH))
        assert second.value.__class__.__name__ == "RateLimited"
    assert route.call_count == 1


@respx.mock
async def test_request_fact_does_not_wedge_a_half_open_probe(
    kyber: KyberSwapClient, clock: FakeClock
) -> None:
    """A 404 on the probe is not an outage; the next request must still reach the provider.

    ``is_open()`` consumes the one half-open permit. Only ``UpstreamError`` used to
    record an outcome, so NotFound/RateLimited/cancellation left the breaker in
    HALF_OPEN with ``probe_in_flight=True`` for ever.
    """
    no_route = {"code": 4011, "message": "token not found"}
    route = respx.get(BASE_ROUTES).mock(
        side_effect=[
            httpx.Response(530),
            httpx.Response(200, json=no_route),
            httpx.Response(200, json=no_route),
        ]
    )
    async with kyber as client:
        with pytest.raises(UpstreamError):
            await client.quote(quote_request(chain="base", token=WETH, amount_usd="100"))
        clock.advance(61)
        with pytest.raises(NotFound):
            await client.quote(quote_request(chain="base", token=WETH, amount_usd="101"))
        # If the probe leaked, this raises "circuit open" and never reaches the route.
        with pytest.raises(NotFound):
            await client.quote(quote_request(chain="base", token=WETH, amount_usd="102"))
    assert route.call_count == 3
