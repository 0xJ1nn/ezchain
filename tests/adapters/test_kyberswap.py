"""KyberSwap quote contract: raw sizing, USD legs, derived impact and no-route handling.

All HTTP is mocked with respx from one real ``/base/api/v1/routes`` recording
(2026-10-22, 100 USDC → WETH).
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx

from ezchain_adapters import DecimalsResolver, KyberSwapClient, NotFound, UpstreamError
from ezchain_adapters.ratelimit import TokenBucket

from .conftest import FakeClock, quote_request

BASE_ROUTES = "https://aggregator-api.kyberswap.com/base/api/v1/routes"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
WETH = "0x4200000000000000000000000000000000000006"


@pytest.fixture
def kyber(clock: FakeClock, fast_limiter: TokenBucket, decimals: DecimalsResolver) -> Any:
    decimals.remember("base", WETH, 18)
    return KyberSwapClient(decimals=decimals, limiter=fast_limiter, clock=clock, sleep=clock.sleep)


@respx.mock
async def test_buy_sizes_the_stable_and_scales_the_token(
    kyber: KyberSwapClient, load_fixture: Callable[[str], Any]
) -> None:
    recorded = load_fixture("kyberswap_base_buy.json")
    route = respx.get(BASE_ROUTES).mock(return_value=httpx.Response(200, json=recorded))
    async with kyber as client:
        quote = await client.quote(quote_request(chain="base", token=WETH))

    assert quote.chain == "base"
    assert quote.side == "buy"
    assert quote.token_in == USDC
    assert quote.token_out == WETH
    assert quote.amount_in == Decimal("100")
    assert quote.amount_out == Decimal("41783066683609720") / Decimal(10**18)
    # price is USD per token: what was paid (the provider's amountInUsd) over tokens bought
    assert quote.price == Decimal("99.9902897791054") / quote.amount_out
    # KyberSwap publishes no impact field: it is derived from the two USD legs
    assert quote.price_impact_pct == (
        Decimal("100.11585308332612") / Decimal("99.9902897791054") - 1
    )
    # gas plus the OP-stack L1 data fee
    assert quote.fee_usd == Decimal("0.008539624477556698") + Decimal(
        "0.00017586302130402854"
    )
    assert quote.kind == "kyberswap"
    assert quote.route == "prism-prop"
    assert quote.source.name == "kyberswap"
    assert quote.raw == recorded

    params = route.calls[0].request.url.params
    assert params["tokenIn"] == USDC
    assert params["tokenOut"] == WETH
    assert params["amountIn"] == "100000000"


@respx.mock
async def test_sell_is_sized_by_quantity_in_token_units(
    kyber: KyberSwapClient, load_fixture: Callable[[str], Any]
) -> None:
    recorded = load_fixture("kyberswap_base_buy.json")
    route = respx.get(BASE_ROUTES).mock(return_value=httpx.Response(200, json=recorded))
    async with kyber as client:
        quote = await client.quote(
            quote_request(chain="base", token=WETH, side="sell", quantity="0.5")
        )

    assert quote.side == "sell"
    assert quote.token_in == WETH
    assert quote.token_out == USDC
    assert quote.amount_in == Decimal("0.5")
    params = route.calls[0].request.url.params
    assert params["tokenIn"] == WETH
    assert params["amountIn"] == "500000000000000000"


@respx.mock
async def test_zero_raw_amount_is_not_found(kyber: KyberSwapClient) -> None:
    respx.get(BASE_ROUTES).mock(return_value=httpx.Response(200, json={"code": 0}))
    async with kyber as client:
        with pytest.raises(NotFound):
            await client.quote(quote_request(chain="base", token=WETH, amount_usd="0.0000001"))


@respx.mock
async def test_route_not_found_code_is_not_found(kyber: KyberSwapClient) -> None:
    respx.get(BASE_ROUTES).mock(
        return_value=httpx.Response(200, json={"code": 4008, "message": "route not found"})
    )
    async with kyber as client:
        with pytest.raises(NotFound):
            await client.quote(quote_request(chain="base", token=WETH))


@respx.mock
async def test_zero_amount_out_is_not_found(
    kyber: KyberSwapClient, load_fixture: Callable[[str], Any]
) -> None:
    recorded = load_fixture("kyberswap_base_buy.json")
    recorded["data"]["routeSummary"]["amountOut"] = "0"
    respx.get(BASE_ROUTES).mock(return_value=httpx.Response(200, json=recorded))
    async with kyber as client:
        with pytest.raises(NotFound):
            await client.quote(quote_request(chain="base", token=WETH))


@respx.mock
async def test_5xx_opens_the_circuit(kyber: KyberSwapClient) -> None:
    route = respx.get(BASE_ROUTES).mock(return_value=httpx.Response(530, text="error code: 1033"))
    async with kyber as client:
        with pytest.raises(UpstreamError):
            await client.quote(quote_request(chain="base", token=WETH))
        with pytest.raises(UpstreamError):
            await client.quote(quote_request(chain="base", token=WETH))
    # the second call never reached the network: the breaker answered
    assert route.call_count == 1


async def test_unknown_decimals_fail_closed(clock: FakeClock, fast_limiter: TokenBucket) -> None:
    client = KyberSwapClient(
        decimals=DecimalsResolver(),
        limiter=fast_limiter,
        clock=clock,
        sleep=clock.sleep,
    )
    async with client:
        with pytest.raises(NotFound):
            await client.quote(quote_request(chain="base", token=WETH))


def test_supports_only_the_verified_chains(kyber: KyberSwapClient) -> None:
    from ezchain_adapters import get_chain

    base = get_chain("base")
    mantle = get_chain("mantle")
    assert base is not None and mantle is not None
    assert kyber.supports(base) is True
    # Mantle was retired from the aggregator (404 verified live): never in its set.
    assert kyber.supports(mantle) is False
