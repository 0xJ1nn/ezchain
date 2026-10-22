"""Jupiter quote contract tests: sizing, decimals, routing, failures.

All HTTP is mocked with respx using trimmed recordings of real Lite API quotes.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx

from ezchain_adapters import JupiterClient, NotFound, RateLimited, UpstreamError
from ezchain_adapters.jupiter import LITE_BASE, SOL_MINT, USDC_MINT

BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
TOKEN_SEARCH = f"{LITE_BASE}/tokens/v2/search"
QUOTE = f"{LITE_BASE}/swap/v1/quote"


def _token_search(mint: str, decimals: Any) -> list[dict[str, Any]]:
    return [{"id": mint, "decimals": decimals, "symbol": "BONK"}]


@respx.mock
async def test_buy_quote_sizes_usdc_and_rescales_token_decimals(
    jupiter: JupiterClient, load_fixture: Callable[[str], Any]
) -> None:
    search_route = respx.get(TOKEN_SEARCH).mock(
        return_value=httpx.Response(
            200, json=load_fixture("jupiter_token_search.json")
        )
    )
    recorded = load_fixture("jupiter_quote_buy.json")
    quote_route = respx.get(QUOTE).mock(
        return_value=httpx.Response(200, json=recorded)
    )
    async with jupiter as client:
        quote, in_decimals, out_decimals = await client.quote_paper(
            token_address=BONK, side="buy", amount_usd=Decimal("1")
        )

    assert (in_decimals, out_decimals) == (6, 5)
    assert quote.chain == "solana"
    assert quote.side == "buy"
    assert quote.token_in == USDC_MINT
    assert quote.token_out == BONK
    assert quote.amount_in == Decimal(1)
    assert quote.amount_out == Decimal("329187.71476")
    assert quote.price == Decimal(1) / Decimal("329187.71476")
    assert quote.price_impact_pct == Decimal(0)
    assert quote.route == "BisonFi > Whirlpool"
    assert quote.kind == "jupiter"
    assert quote.source.name == "jupiter"
    assert quote.raw == recorded

    params = quote_route.calls[0].request.url.params
    assert params["inputMint"] == USDC_MINT
    assert params["outputMint"] == BONK
    assert params["amount"] == "1000000"
    assert params["slippageBps"] == "50"
    assert search_route.call_count == 1


@respx.mock
async def test_sell_quote_sizes_quantity_and_rescales_usdc(
    jupiter: JupiterClient, load_fixture: Callable[[str], Any]
) -> None:
    respx.get(TOKEN_SEARCH).mock(
        return_value=httpx.Response(
            200, json=load_fixture("jupiter_token_search.json")
        )
    )
    recorded = load_fixture("jupiter_quote_sell.json")
    quote_route = respx.get(QUOTE).mock(
        return_value=httpx.Response(200, json=recorded)
    )
    async with jupiter as client:
        quote, in_decimals, out_decimals = await client.quote_paper(
            token_address=BONK,
            side="sell",
            amount_usd=Decimal(1),
            quantity=Decimal("100000"),
        )

    assert (in_decimals, out_decimals) == (5, 6)
    assert quote.side == "sell"
    assert quote.token_in == BONK
    assert quote.token_out == USDC_MINT
    assert quote.amount_in == Decimal(100000)
    assert quote.amount_out == Decimal("0.303564")
    assert quote.price == Decimal("0.303564") / Decimal(100000)
    assert quote.price_impact_pct == Decimal("0.0012288989442247177355302683")
    assert quote.route == "Whirlpool > Invariant"

    params = quote_route.calls[0].request.url.params
    assert params["inputMint"] == BONK
    assert params["outputMint"] == USDC_MINT
    assert params["amount"] == "10000000000"


@respx.mock
async def test_known_mints_need_no_decimals_lookup(jupiter: JupiterClient) -> None:
    async with jupiter as client:
        assert await client.token_decimals(USDC_MINT) == 6
        assert await client.token_decimals(SOL_MINT) == 9


@respx.mock
async def test_repeated_quotes_are_cached(jupiter: JupiterClient) -> None:
    search_route = respx.get(TOKEN_SEARCH).mock(
        return_value=httpx.Response(200, json=_token_search(BONK, 5))
    )
    quote_route = respx.get(QUOTE).mock(
        return_value=httpx.Response(200, json={"outAmount": "100000"})
    )
    async with jupiter as client:
        first = await client.quote_paper(
            token_address=BONK, side="buy", amount_usd=Decimal("1")
        )
        second = await client.quote_paper(
            token_address=BONK, side="buy", amount_usd=Decimal("1")
        )
    assert search_route.call_count == 1
    assert quote_route.call_count == 1
    assert first[0].amount_out == second[0].amount_out
    assert first[0].raw == second[0].raw


@respx.mock
async def test_buy_without_route_is_not_found(jupiter: JupiterClient) -> None:
    respx.get(TOKEN_SEARCH).mock(
        return_value=httpx.Response(200, json=_token_search(BONK, 5))
    )
    respx.get(QUOTE).mock(
        return_value=httpx.Response(400, json={"error": "Could not find any route"})
    )
    async with jupiter as client:
        with pytest.raises(NotFound):
            await client.quote_paper(token_address=BONK, side="buy", amount_usd=Decimal(1))


@respx.mock
async def test_zero_out_amount_is_not_found(jupiter: JupiterClient) -> None:
    respx.get(TOKEN_SEARCH).mock(
        return_value=httpx.Response(200, json=_token_search(BONK, 5))
    )
    respx.get(QUOTE).mock(return_value=httpx.Response(200, json={"outAmount": "0"}))
    async with jupiter as client:
        with pytest.raises(NotFound):
            await client.quote_paper(token_address=BONK, side="buy", amount_usd=Decimal(1))


@pytest.mark.parametrize(
    "payload",
    [
        {"outAmount": "not-a-number"},
        {"outAmount": "-5"},
        {"outAmount": None},
        {"outAmount": []},
        {"noOutAmount": "1"},
        [],
        "route",
    ],
)
@respx.mock
async def test_malformed_quote_payload_is_an_upstream_error(
    jupiter: JupiterClient, payload: Any
) -> None:
    respx.get(TOKEN_SEARCH).mock(
        return_value=httpx.Response(200, json=_token_search(BONK, 5))
    )
    respx.get(QUOTE).mock(return_value=httpx.Response(200, json=payload))
    async with jupiter as client:
        with pytest.raises(UpstreamError):
            await client.quote_paper(token_address=BONK, side="buy", amount_usd=Decimal(1))


@respx.mock
async def test_malformed_quote_json_is_an_upstream_error(jupiter: JupiterClient) -> None:
    respx.get(TOKEN_SEARCH).mock(
        return_value=httpx.Response(200, json=_token_search(BONK, 5))
    )
    respx.get(QUOTE).mock(return_value=httpx.Response(200, content=b"not json"))
    async with jupiter as client:
        with pytest.raises(UpstreamError):
            await client.quote_paper(token_address=BONK, side="buy", amount_usd=Decimal(1))


@respx.mock
async def test_optional_route_and_impact_are_allowed(
    jupiter: JupiterClient, load_fixture: Callable[[str], Any]
) -> None:
    respx.get(TOKEN_SEARCH).mock(
        return_value=httpx.Response(
            200, json=load_fixture("jupiter_token_search.json")
        )
    )
    payload = copy.deepcopy(load_fixture("jupiter_quote_buy.json"))
    payload.pop("routePlan")
    payload.pop("priceImpactPct")
    respx.get(QUOTE).mock(return_value=httpx.Response(200, json=payload))
    async with jupiter as client:
        quote, _, _ = await client.quote_paper(
            token_address=BONK, side="buy", amount_usd=Decimal(1)
        )
    assert quote.route is None
    assert quote.price_impact_pct is None


@respx.mock
async def test_malformed_route_plan_is_ignored(
    jupiter: JupiterClient, load_fixture: Callable[[str], Any]
) -> None:
    respx.get(TOKEN_SEARCH).mock(
        return_value=httpx.Response(
            200, json=load_fixture("jupiter_token_search.json")
        )
    )
    payload = copy.deepcopy(load_fixture("jupiter_quote_buy.json"))
    payload["routePlan"] = {"not": "a list"}
    respx.get(QUOTE).mock(return_value=httpx.Response(200, json=payload))
    async with jupiter as client:
        quote, _, _ = await client.quote_paper(
            token_address=BONK, side="buy", amount_usd=Decimal(1)
        )
    assert quote.route is None


@respx.mock
async def test_unknown_token_decimals_are_not_found(jupiter: JupiterClient) -> None:
    respx.get(TOKEN_SEARCH).mock(return_value=httpx.Response(200, json=[]))
    async with jupiter as client:
        with pytest.raises(NotFound):
            await client.token_decimals(BONK)


@pytest.mark.parametrize(
    "payload",
    [
        {"not": "a list"},
        "tokens",
        [{"id": BONK, "decimals": "abc"}],
        [{"id": BONK, "decimals": 19}],
        [{"id": BONK, "decimals": None}],
        [{"id": BONK, "decimals": True}],
    ],
)
@respx.mock
async def test_malformed_token_search_payload_is_an_upstream_error(
    jupiter: JupiterClient, payload: Any
) -> None:
    respx.get(TOKEN_SEARCH).mock(return_value=httpx.Response(200, json=payload))
    async with jupiter as client:
        with pytest.raises(UpstreamError):
            await client.token_decimals(BONK)


@respx.mock
async def test_token_missing_from_search_is_not_found(jupiter: JupiterClient) -> None:
    payload = [{"id": "different-mint", "decimals": 5}]
    respx.get(TOKEN_SEARCH).mock(return_value=httpx.Response(200, json=payload))
    async with jupiter as client:
        with pytest.raises(NotFound):
            await client.token_decimals(BONK)


@respx.mock
async def test_sell_without_quantity_fails_before_any_request(
    jupiter: JupiterClient,
) -> None:
    async with jupiter as client:
        with pytest.raises(NotFound):
            await client.quote_paper(
                token_address=BONK, side="sell", amount_usd=Decimal(1)
            )


@respx.mock
async def test_buy_with_zero_notional_fails_before_any_request(
    jupiter: JupiterClient,
) -> None:
    async with jupiter as client:
        with pytest.raises(NotFound):
            await client.quote_paper(
                token_address=BONK, side="buy", amount_usd=Decimal(0)
            )


@respx.mock
async def test_unknown_side_is_rejected(jupiter: JupiterClient) -> None:
    async with jupiter as client:
        with pytest.raises(ValueError):
            await client.quote_paper(
                token_address=BONK, side="short", amount_usd=Decimal(1)
            )


@respx.mock
async def test_429_sets_a_cooldown_and_fails_fast(
    jupiter: JupiterClient, clock: Any
) -> None:
    respx.get(TOKEN_SEARCH).mock(
        return_value=httpx.Response(200, json=_token_search(BONK, 5))
    )
    quote_route = respx.get(QUOTE).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "30"}),
            httpx.Response(200, json={"outAmount": "100000"}),
        ]
    )
    async with jupiter as client:
        with pytest.raises(RateLimited):
            await client.quote_paper(
                token_address=BONK, side="buy", amount_usd=Decimal(1)
            )
        with pytest.raises(RateLimited):
            await client.quote_paper(
                token_address=BONK, side="buy", amount_usd=Decimal(1)
            )
        assert quote_route.call_count == 1
        clock.advance(30.5)
        quote, _, _ = await client.quote_paper(
            token_address=BONK, side="buy", amount_usd=Decimal(1)
        )
        assert quote_route.call_count == 2
    assert quote.amount_out == Decimal(1)  # 100000 raw / BONK 5 decimals


@respx.mock
async def test_5xx_is_an_upstream_error(jupiter: JupiterClient) -> None:
    respx.get(TOKEN_SEARCH).mock(
        return_value=httpx.Response(200, json=_token_search(BONK, 5))
    )
    respx.get(QUOTE).mock(return_value=httpx.Response(503))
    async with jupiter as client:
        with pytest.raises(UpstreamError):
            await client.quote_paper(token_address=BONK, side="buy", amount_usd=Decimal(1))


@respx.mock
async def test_network_failure_is_an_upstream_error(jupiter: JupiterClient) -> None:
    respx.get(TOKEN_SEARCH).mock(
        return_value=httpx.Response(200, json=_token_search(BONK, 5))
    )
    respx.get(QUOTE).mock(side_effect=httpx.ConnectError("offline"))
    async with jupiter as client:
        with pytest.raises(UpstreamError):
            await client.quote_paper(token_address=BONK, side="buy", amount_usd=Decimal(1))
