"""Cetus, STON.fi and Osmosis SQS quote contracts (Sui, TON, Cosmos).

Each test replays one real recording and asserts the provider-specific shape: Cetus's
``deviation_ratio``, STON.fi's query-string POST and local native-TON decimals, SQS's
``amount+denom`` tokenIn and liquidity-cap guard.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx

from ezchain_adapters import (
    CetusClient,
    DecimalsResolver,
    NotFound,
    OsmosisSqsClient,
    StonfiClient,
)
from ezchain_adapters.ratelimit import TokenBucket

from .conftest import FakeClock, quote_request

CETUS = "https://api-sui.cetus.zone/router_v2/find_routes"
STON = "https://api.ston.fi/v1/swap/simulate"
SQS = "https://sqs.osmosis.zone/router/quote"

SUI = "0x2::sui::SUI"
SUI_USDC = "0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC"
TON_NATIVE = "EQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAM9c"
TON_USDT = "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"
OSMO_USDC = "ibc/27394FB092D2ECCD56123C74F36E4C1F926001CEADA9CA97EA622B25F41E5EB2"


@pytest.fixture
def cetus(clock: FakeClock, fast_limiter: TokenBucket, decimals: DecimalsResolver) -> Any:
    decimals.remember("sui", SUI, 9)
    return CetusClient(decimals=decimals, limiter=fast_limiter, clock=clock, sleep=clock.sleep)


@pytest.fixture
def stonfi(clock: FakeClock, fast_limiter: TokenBucket, decimals: DecimalsResolver) -> Any:
    return StonfiClient(decimals=decimals, limiter=fast_limiter, clock=clock, sleep=clock.sleep)


@pytest.fixture
def sqs(clock: FakeClock, fast_limiter: TokenBucket, decimals: DecimalsResolver) -> Any:
    return OsmosisSqsClient(decimals=decimals, limiter=fast_limiter, clock=clock, sleep=clock.sleep)


@respx.mock
async def test_cetus_buy_uses_deviation_ratio_as_impact(
    cetus: CetusClient, load_fixture: Callable[[str], Any]
) -> None:
    recorded = load_fixture("cetus_sui_buy.json")
    route = respx.get(CETUS).mock(return_value=httpx.Response(200, json=recorded))
    async with cetus as client:
        quote = await client.quote(quote_request(chain="sui", token=SUI, amount_usd="1"))

    assert quote.kind == "cetus"
    assert quote.token_in == SUI_USDC
    assert quote.token_out == SUI
    assert quote.amount_in == Decimal("1")
    assert quote.amount_out == Decimal("1.021254006")
    assert quote.price_impact_pct == Decimal("-0.00099204")
    assert quote.fee_usd == Decimal(0)
    assert quote.route == "AFTERMATH"
    params = route.calls[0].request.url.params
    assert params["amount"] == "1000000"
    assert params["byAmountIn"] == "true"


@respx.mock
async def test_cetus_error_code_is_no_route(cetus: CetusClient) -> None:
    respx.get(CETUS).mock(return_value=httpx.Response(200, json={"code": 500, "msg": "no route"}))
    async with cetus as client:
        with pytest.raises(NotFound):
            await client.quote(quote_request(chain="sui", token=SUI))


@respx.mock
async def test_stonfi_buy_posts_query_params_and_knows_native_ton(
    stonfi: StonfiClient, load_fixture: Callable[[str], Any]
) -> None:
    recorded = load_fixture("stonfi_ton_buy.json")
    route = respx.post(STON).mock(return_value=httpx.Response(200, json=recorded))
    async with stonfi as client:
        quote = await client.quote(quote_request(chain="ton", token=TON_NATIVE, amount_usd="1"))

    assert quote.kind == "stonfi"
    assert quote.token_in == TON_USDT
    assert quote.token_out == TON_NATIVE
    assert quote.amount_in == Decimal("1")
    # native TON is 9 decimals; the table supplies it because GeckoTerminal cannot
    assert quote.amount_out == Decimal("752067442") / Decimal(10**9)
    assert quote.price_impact_pct == Decimal("0.000000404")
    assert quote.route == "STON.fi v2"

    request = route.calls[0].request
    assert request.method == "POST"
    params = request.url.params
    assert params["offer_address"] == TON_USDT
    assert params["ask_address"] == TON_NATIVE
    assert params["units"] == "1000000"


@respx.mock
async def test_stonfi_missing_ask_units_is_no_route(stonfi: StonfiClient) -> None:
    respx.post(STON).mock(
        return_value=httpx.Response(200, json={"error": "pool not found", "offer_units": "1"})
    )
    async with stonfi as client:
        with pytest.raises(NotFound):
            await client.quote(quote_request(chain="ton", token=TON_NATIVE))


@respx.mock
async def test_sqs_buy_concatenates_amount_and_denom(
    sqs: OsmosisSqsClient, load_fixture: Callable[[str], Any]
) -> None:
    recorded = load_fixture("osmosis_sqs_buy.json")
    route = respx.get(SQS).mock(return_value=httpx.Response(200, json=recorded))
    async with sqs as client:
        quote = await client.quote(quote_request(chain="osmosis", token="uosmo", amount_usd="1"))

    assert quote.kind == "osmosis_sqs"
    assert quote.token_in == OSMO_USDC
    assert quote.token_out == "uosmo"
    assert quote.amount_in == Decimal("1")
    assert quote.amount_out == Decimal("34.839716")
    assert quote.price_impact_pct == Decimal("-0.005144777269526796")
    assert quote.route == "pool-10 > pool-9"
    params = route.calls[0].request.url.params
    assert params["tokenIn"] == f"1000000{OSMO_USDC}"
    assert params["tokenOutDenom"] == "uosmo"


@respx.mock
async def test_sqs_liquidity_cap_overflow_is_no_route(
    sqs: OsmosisSqsClient, load_fixture: Callable[[str], Any]
) -> None:
    recorded = load_fixture("osmosis_sqs_buy.json")
    recorded["liquidity_cap_overflow"] = True
    respx.get(SQS).mock(return_value=httpx.Response(200, json=recorded))
    async with sqs as client:
        with pytest.raises(NotFound):
            await client.quote(quote_request(chain="osmosis", token="uosmo"))


async def test_sqs_unknown_denom_fails_closed(
    clock: FakeClock, fast_limiter: TokenBucket, decimals: DecimalsResolver
) -> None:
    client = OsmosisSqsClient(
        decimals=decimals, limiter=fast_limiter, clock=clock, sleep=clock.sleep
    )
    async with client:
        with pytest.raises(NotFound):
            await client.quote(
                quote_request(chain="osmosis", token="factory/osmo1xyz/utoken", amount_usd="1")
            )
