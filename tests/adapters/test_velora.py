"""Velora quote contract: zero partner fee, destAmountAfterFee, impact budget, gas cost."""

from __future__ import annotations

import copy
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx

from ezchain_adapters import DecimalsResolver, NotFound, VeloraClient
from ezchain_adapters.ratelimit import TokenBucket

from .conftest import FakeClock, quote_request

PRICES = "https://api.paraswap.io/prices"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
WETH = "0x4200000000000000000000000000000000000006"


@pytest.fixture
def velora(clock: FakeClock, fast_limiter: TokenBucket, decimals: DecimalsResolver) -> Any:
    decimals.remember("base", WETH, 18)
    return VeloraClient(decimals=decimals, limiter=fast_limiter, clock=clock, sleep=clock.sleep)


@respx.mock
async def test_buy_uses_the_after_fee_amount_and_declares_gas(
    velora: VeloraClient, load_fixture: Callable[[str], Any]
) -> None:
    recorded = load_fixture("velora_base_buy.json")
    route = respx.get(PRICES).mock(return_value=httpx.Response(200, json=recorded))
    async with velora as client:
        quote = await client.quote(quote_request(chain="base", token=WETH))

    assert quote.kind == "velora"
    assert quote.token_in == USDC
    assert quote.token_out == WETH
    assert quote.amount_in == Decimal("100")
    assert quote.amount_out == Decimal("41772625698527951") / Decimal(10**18)
    assert quote.fee_usd == Decimal("0.004380")
    assert quote.price_impact_pct == (
        Decimal("100.1248065368") / Decimal("99.9612000000") - 1
    )
    assert quote.route == "aerodromeslipstream"

    params = route.calls[0].request.url.params
    # exact-in in both directions; the partner fee is zeroed so the fill is not 1 bp short
    assert params["side"] == "SELL"
    assert params["partnerFeeBps"] == "0"
    assert params["network"] == "8453"
    assert params["srcDecimals"] == "6"
    assert params["destDecimals"] == "18"


@respx.mock
async def test_after_fee_amount_wins_and_the_delta_is_declared_as_fee(
    velora: VeloraClient, load_fixture: Callable[[str], Any]
) -> None:
    recorded = copy.deepcopy(load_fixture("velora_base_buy.json"))
    price_route = recorded["priceRoute"]
    price_route["destAmount"] = "41772625698527951"
    price_route["destAmountAfterFee"] = "41768453089988100"
    respx.get(PRICES).mock(return_value=httpx.Response(200, json=recorded))
    async with velora as client:
        quote = await client.quote(quote_request(chain="base", token=WETH))

    assert quote.amount_out == Decimal("41768453089988100") / Decimal(10**18)
    # gas (0.004380) plus the residual partner fee valued at destUSD
    share = (Decimal("41772625698527951") - Decimal("41768453089988100")) / Decimal(
        "41772625698527951"
    )
    assert quote.fee_usd == Decimal("0.004380") + Decimal("100.1248065368") * share


@respx.mock
async def test_reached_impact_budget_is_no_route(
    velora: VeloraClient, load_fixture: Callable[[str], Any]
) -> None:
    recorded = copy.deepcopy(load_fixture("velora_base_buy.json"))
    recorded["priceRoute"]["maxImpactReached"] = True
    respx.get(PRICES).mock(return_value=httpx.Response(200, json=recorded))
    async with velora as client:
        with pytest.raises(NotFound):
            await client.quote(quote_request(chain="base", token=WETH))


@respx.mock
async def test_sell_sizes_by_quantity(velora: VeloraClient) -> None:
    respx.get(PRICES).mock(return_value=httpx.Response(400, json={"error": "Invalid amount"}))
    async with velora as client:
        with pytest.raises(NotFound):
            await client.quote(
                quote_request(chain="base", token=WETH, side="sell", quantity="0.25")
            )
