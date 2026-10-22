"""LI.FI quote contract: 0.25% fee declared, chain-id params, 75-per-2h budget."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx

from ezchain_adapters import DecimalsResolver, LifiClient, NotFound, RateLimited
from ezchain_adapters.lifi import QUOTE_ADDRESS
from ezchain_adapters.ratelimit import TokenBucket

from .conftest import FakeClock, quote_request

QUOTE = "https://li.quest/v1/quote"
USDC = "0x09Bc4E0D864854c6aFB6eB9A9cdF58aC190D0dF9"
WETH = "0xdEAddEaDdeadDEadDEADDEAddEADDEAddead1111"


@pytest.fixture
def lifi(clock: FakeClock, fast_limiter: TokenBucket, decimals: DecimalsResolver) -> Any:
    decimals.remember("mantle", WETH, 18)
    return LifiClient(decimals=decimals, limiter=fast_limiter, clock=clock, sleep=clock.sleep)


@respx.mock
async def test_buy_declares_the_fixed_fee_and_gas(
    lifi: LifiClient, load_fixture: Callable[[str], Any]
) -> None:
    recorded = load_fixture("lifi_mantle_buy.json")
    route = respx.get(QUOTE).mock(return_value=httpx.Response(200, json=recorded))
    async with lifi as client:
        quote = await client.quote(quote_request(chain="mantle", token=WETH, amount_usd="1"))

    assert quote.kind == "lifi"
    assert quote.token_in == USDC
    assert quote.token_out == WETH
    assert quote.amount_in == Decimal("1")
    assert quote.amount_out == Decimal("414267317972366") / Decimal(10**18)
    # 0.25% LI.FI fee plus gas, both already inside toAmount; declared, never added twice
    assert quote.fee_usd == Decimal("0.0025") + Decimal("0.0154")
    assert quote.price_impact_pct == Decimal("0.9843") / Decimal("0.9986") - 1
    assert quote.route == "SushiSwap Aggregator"

    params = route.calls[0].request.url.params
    assert params["fromChain"] == "5000"
    assert params["toChain"] == "5000"
    assert params["fromAddress"] == QUOTE_ADDRESS
    assert params["fromAmount"] == "1000000"


@respx.mock
async def test_message_only_payload_is_no_route(lifi: LifiClient) -> None:
    respx.get(QUOTE).mock(
        return_value=httpx.Response(
            200, json={"message": "Could not find token", "code": 1003}
        )
    )
    async with lifi as client:
        with pytest.raises(NotFound):
            await client.quote(quote_request(chain="mantle", token=WETH))


async def test_budget_fails_fast_instead_of_sleeping(
    clock: FakeClock, decimals: DecimalsResolver
) -> None:
    """The free tier is 75 requests / 2 h: the fourth call inside a window is refused."""
    decimals.remember("mantle", WETH, 18)
    client = LifiClient(decimals=decimals, clock=clock, sleep=clock.sleep)
    with respx.mock:
        respx.get(QUOTE).mock(return_value=httpx.Response(200, json={"estimate": {}}))
        async with client:
            for amount in ("1", "2", "3"):
                with pytest.raises(NotFound):
                    await client.quote(
                        quote_request(chain="mantle", token=WETH, amount_usd=amount)
                    )
            with pytest.raises(RateLimited):
                await client.quote(quote_request(chain="mantle", token=WETH, amount_usd="4"))
    assert clock.sleeps == []
