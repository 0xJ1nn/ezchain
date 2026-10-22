"""Decimals resolution: registry stable, provider value, GeckoTerminal, else fail closed."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx

from ezchain_adapters import (
    DecimalsResolver,
    GeckoTerminalClient,
    NotFound,
    UpstreamError,
    get_chain,
)
from ezchain_adapters.ratelimit import TokenBucket

from .conftest import FakeClock

TOKEN = "https://api.geckoterminal.com/api/v2/networks/base/tokens/0x4200000000000000000000000000000000000006"
WETH = "0x4200000000000000000000000000000000000006"


def test_registry_stable_needs_no_lookup(clock: FakeClock) -> None:
    base = get_chain("base")
    assert base is not None and base.quote_asset is not None
    resolver = DecimalsResolver()
    # No metadata client at all: the stable's decimals come from the registry.
    assert _run(resolver.decimals(base, base.quote_asset.address)) == base.quote_asset.decimals


def test_remembered_value_is_used_before_metadata(clock: FakeClock) -> None:
    base = get_chain("base")
    assert base is not None
    resolver = DecimalsResolver()
    resolver.remember("base", WETH, 18)
    assert _run(resolver.decimals(base, WETH)) == 18


def test_unknown_without_a_metadata_client_fails_closed() -> None:
    base = get_chain("base")
    assert base is not None
    resolver = DecimalsResolver()
    with pytest.raises(NotFound):
        _run(resolver.decimals(base, WETH))


def test_chain_without_a_geckoterminal_network_fails_closed() -> None:
    osmosis = get_chain("osmosis")
    assert osmosis is not None
    resolver = DecimalsResolver()
    with pytest.raises(NotFound):
        _run(resolver.decimals(osmosis, "uosmo"))


@respx.mock
async def test_geckoterminal_decimals_are_read_and_cached(
    clock: FakeClock, fast_limiter: TokenBucket, load_fixture: Callable[[str], Any]
) -> None:
    base = get_chain("base")
    assert base is not None
    route = respx.get(TOKEN).mock(
        return_value=httpx.Response(200, json=load_fixture("geckoterminal_base_weth.json"))
    )
    gecko = GeckoTerminalClient(limiter=fast_limiter, clock=clock, sleep=clock.sleep)
    resolver = DecimalsResolver(gecko)
    async with gecko:
        assert await resolver.decimals(base, WETH) == 18
        assert await resolver.decimals(base, WETH) == 18
    # the resolver remembers, so the second call never asks again
    assert route.call_count == 1


@respx.mock
async def test_geckoterminal_malformed_decimals_is_an_upstream_error(
    clock: FakeClock, fast_limiter: TokenBucket
) -> None:
    route = respx.get(TOKEN).mock(
        return_value=httpx.Response(200, json={"data": {"attributes": {"decimals": "eighteen"}}})
    )
    gecko = GeckoTerminalClient(limiter=fast_limiter, clock=clock, sleep=clock.sleep)
    async with gecko:
        with pytest.raises(UpstreamError):
            await gecko.token_decimals("base", WETH)
    assert route.call_count == 1


@respx.mock
async def test_geckoterminal_missing_token_is_not_found(
    clock: FakeClock, fast_limiter: TokenBucket
) -> None:
    respx.get(TOKEN).mock(return_value=httpx.Response(404, json={"errors": []}))
    gecko = GeckoTerminalClient(limiter=fast_limiter, clock=clock, sleep=clock.sleep)
    async with gecko:
        with pytest.raises(NotFound):
            await gecko.token_decimals("base", WETH)


def _run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)
