"""GeckoTerminal OHLCV contract: parsing, ordering, timeframe mapping, pacing, failures.

All HTTP is mocked with respx. The recorded fixture is one real ``hour`` response for the
WETH/USDC 0.05% pool on Ethereum captured on 2026-10-22; nothing here reaches the network.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx

from ezchain_adapters import (
    CandlesUnsupported,
    GeckoTerminalClient,
    NotFound,
    RateLimited,
    UpstreamError,
    parse_ohlcv,
)
from ezchain_adapters.geckoterminal import (
    BASE,
    MAX_LIMIT,
    RETRY_BACKOFF_SECONDS,
    TIMEFRAMES,
    timeframe_seconds,
)

WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
POOL = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"
HOUR_URL = rf"^{re.escape(BASE)}/networks/eth/pools/{re.escape(POOL)}/ohlcv/hour.*$"
MINUTE_URL = rf"^{re.escape(BASE)}/networks/eth/pools/{re.escape(POOL)}/ohlcv/minute.*$"

# 1792681200 is 2026-10-22T15:00:00Z, the newest (forming or just-closed) recorded bar.
NEWEST_OPEN = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)


def _response(reference: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=copy.deepcopy(reference))


def test_public_rate_budget_is_respected() -> None:
    assert round(GeckoTerminalClient.rate_per_second * 60) == 25
    assert 1 <= GeckoTerminalClient.burst <= 25


def test_recorded_response_parses_oldest_first(load_fixture: Callable[[str], Any]) -> None:
    payload = load_fixture("geckoterminal_ohlcv_eth_weth_hour.json")
    # The provider sends newest first; the adapter returns oldest first.
    raw = payload["data"]["attributes"]["ohlcv_list"]
    assert raw[0][0] > raw[-1][0]

    candles = parse_ohlcv(payload, 3_600)

    assert len(candles) == len(raw) == 5
    opens = [candle.open_time for candle in candles]
    assert opens == sorted(opens)
    assert opens[-1] == NEWEST_OPEN
    assert all(candle.timeframe_seconds == 3_600 for candle in candles)
    assert candles[-1].open == Decimal("2407.33")
    assert candles[-1].high == Decimal("2411.29")
    assert candles[-1].low == Decimal("2389.93")
    assert candles[-1].close == Decimal("2391.59")
    assert candles[-1].volume == Decimal("3001406.7053909977")
    assert candles[-1].close_time == datetime(2026, 10, 22, 16, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("timeframe", "unit", "aggregate", "seconds"),
    [
        ("1m", "minute", 1, 60),
        ("5m", "minute", 5, 300),
        ("15m", "minute", 15, 900),
        ("1h", "hour", 1, 3_600),
        ("4h", "hour", 4, 14_400),
        ("1d", "day", 1, 86_400),
    ],
)
def test_exposed_timeframes_map_to_provider_units(
    timeframe: str, unit: str, aggregate: int, seconds: int
) -> None:
    assert TIMEFRAMES[timeframe] == (unit, aggregate)
    assert timeframe_seconds(timeframe) == seconds


@respx.mock
async def test_request_carries_our_token_and_timeframe(
    geckoterminal: GeckoTerminalClient, load_fixture: Callable[[str], Any]
) -> None:
    route = respx.get(url__regex=MINUTE_URL).mock(
        return_value=_response(load_fixture("geckoterminal_ohlcv_eth_weth_hour.json"))
    )
    async with geckoterminal as client:
        candles = await client.ohlcv("ethereum", POOL, WETH, "5m", limit=5)

    assert len(candles) == 5
    params = route.calls[0].request.url.params
    assert params["aggregate"] == "5"
    assert params["limit"] == "5"
    assert params["currency"] == "usd"
    # The price must belong to OUR token, not to the pool's base token.
    assert params["token"] == WETH
    assert "before_timestamp" not in params


@respx.mock
async def test_limit_is_capped_at_the_provider_maximum(
    geckoterminal: GeckoTerminalClient, load_fixture: Callable[[str], Any]
) -> None:
    route = respx.get(url__regex=HOUR_URL).mock(
        return_value=_response(load_fixture("geckoterminal_ohlcv_eth_weth_hour.json"))
    )
    async with geckoterminal as client:
        await client.ohlcv("ethereum", POOL, WETH, "1h", limit=5_000)

    assert route.calls[0].request.url.params["limit"] == str(MAX_LIMIT)


@respx.mock
async def test_second_identical_call_is_served_from_cache(
    geckoterminal: GeckoTerminalClient, load_fixture: Callable[[str], Any]
) -> None:
    route = respx.get(url__regex=HOUR_URL).mock(
        return_value=_response(load_fixture("geckoterminal_ohlcv_eth_weth_hour.json"))
    )
    async with geckoterminal as client:
        first = await client.ohlcv("ethereum", POOL, WETH, "1h", limit=5)
        second = await client.ohlcv("ethereum", POOL, WETH, "1h", limit=5)

    assert first == second
    assert route.call_count == 1


@respx.mock
async def test_rate_limit_is_retried_with_backoff(
    geckoterminal: GeckoTerminalClient,
    clock: Any,
    load_fixture: Callable[[str], Any],
) -> None:
    route = respx.get(url__regex=HOUR_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            _response(load_fixture("geckoterminal_ohlcv_eth_weth_hour.json")),
        ]
    )
    async with geckoterminal as client:
        candles = await client.ohlcv("ethereum", POOL, WETH, "1h", limit=5)

    assert len(candles) == 5
    assert route.call_count == 2
    assert clock.sleeps == [RETRY_BACKOFF_SECONDS[0]]


@respx.mock
async def test_repeated_rate_limits_are_bounded(
    geckoterminal: GeckoTerminalClient, clock: Any
) -> None:
    route = respx.get(url__regex=HOUR_URL).mock(return_value=httpx.Response(429))
    async with geckoterminal as client:
        with pytest.raises(RateLimited):
            await client.ohlcv("ethereum", POOL, WETH, "1h", limit=5)

    assert route.call_count == len(RETRY_BACKOFF_SECONDS) + 1
    assert clock.sleeps == list(RETRY_BACKOFF_SECONDS)


@respx.mock
async def test_before_is_forwarded_as_before_timestamp(
    geckoterminal: GeckoTerminalClient, load_fixture: Callable[[str], Any]
) -> None:
    """Walking back one page at a time is the service's job; the adapter passes the bound."""
    route = respx.get(url__regex=HOUR_URL).mock(
        return_value=_response(load_fixture("geckoterminal_ohlcv_eth_weth_hour.json"))
    )
    bound = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    async with geckoterminal as client:
        await client.ohlcv("ethereum", POOL, WETH, "1h", limit=5, before=bound)
        await client.ohlcv("ethereum", POOL, WETH, "1h", limit=5, before=int(bound.timestamp()))

    assert route.call_count == 1, "identical bounds must share one cached response"
    assert route.calls[0].request.url.params["before_timestamp"] == str(int(bound.timestamp()))


@respx.mock
async def test_short_history_returns_what_exists(
    geckoterminal: GeckoTerminalClient, load_fixture: Callable[[str], Any]
) -> None:
    route = respx.get(url__regex=HOUR_URL).mock(
        return_value=_response(load_fixture("geckoterminal_ohlcv_eth_weth_hour.json"))
    )
    async with geckoterminal as client:
        candles = await client.ohlcv("ethereum", POOL, WETH, "1h", limit=50)

    assert len(candles) == 5
    assert route.call_count == 1


async def test_unknown_chain_is_not_found(geckoterminal: GeckoTerminalClient) -> None:
    async with geckoterminal as client:
        with pytest.raises(NotFound):
            await client.ohlcv("not-a-chain", POOL, WETH, "1h", limit=5)


async def test_chain_without_a_provider_fails_closed(geckoterminal: GeckoTerminalClient) -> None:
    """Osmosis is registered but has no GeckoTerminal network: no fabricated history."""
    async with geckoterminal as client:
        with pytest.raises(CandlesUnsupported) as raised:
            await client.ohlcv("osmosis", POOL, WETH, "1h", limit=5)

    assert raised.value.code == "CANDLES_UNSUPPORTED_CHAIN"


async def test_unknown_timeframe_fails_closed(geckoterminal: GeckoTerminalClient) -> None:
    async with geckoterminal as client:
        with pytest.raises(CandlesUnsupported):
            await client.ohlcv("ethereum", POOL, WETH, "7m", limit=5)


@pytest.mark.parametrize(
    "row",
    [
        [NEWEST_OPEN.timestamp(), "1", "3", "1"],  # too few fields
        ["not-a-time", "1", "3", "1", "2", "10"],  # open time is not numeric
        [1_792_681_200, None, "3", "1", "2", "10"],  # missing open price
        [1_792_681_200, "0", "3", "1", "2", "10"],  # non-positive price
        [1_792_681_200, "1", "3", "1", "NaN", "10"],  # non-finite price
    ],
)
def test_malformed_rows_raise_instead_of_being_skipped(row: list[Any]) -> None:
    payload = {"data": {"attributes": {"ohlcv_list": [row]}}}
    with pytest.raises(UpstreamError):
        parse_ohlcv(payload, 3_600)


def test_missing_volume_stays_unknown() -> None:
    payload = {"data": {"attributes": {"ohlcv_list": [[1_792_681_200, "1", "3", "1", "2", None]]}}}
    (candle,) = parse_ohlcv(payload, 3_600)
    assert candle.volume is None


def test_empty_list_is_an_empty_series() -> None:
    assert parse_ohlcv({"data": {"attributes": {"ohlcv_list": []}}}, 3_600) == ()
    assert parse_ohlcv({"data": {"attributes": {}}}, 3_600) == ()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": None},
        {"data": {"attributes": {"ohlcv_list": "nope"}}},
        {"data": {"attributes": None}},
    ],
)
def test_malformed_payloads_raise(payload: dict[str, Any]) -> None:
    with pytest.raises(UpstreamError):
        parse_ohlcv(payload, 3_600)


def test_registry_exposes_candle_capability() -> None:
    from ezchain_adapters.registry import CHAINS, get_chain

    ethereum = get_chain("ethereum")
    osmosis = get_chain("osmosis")
    assert ethereum is not None and osmosis is not None
    assert ethereum.to_dict()["capabilities"] == {
        "browse": True,
        "paper_execute": True,
        "live_execute": False,
        "quote_kind": "kyberswap",
        "quote_providers": ["kyberswap", "velora", "lifi"],
        "executable_quotes": True,
        "candles": True,
    }
    assert osmosis.to_dict()["capabilities"]["candles"] is False  # type: ignore[index]

    ids = [chain.geckoterminal_id for chain in CHAINS if chain.geckoterminal_id is not None]
    assert len(ids) == len(set(ids)), "one provider network per chain"


@pytest.mark.parametrize(
    ("chain_id", "network"),
    [
        ("ethereum", "eth"),
        ("polygon", "polygon_pos"),
        ("avalanche", "avax"),
        ("moonbeam", "glmr"),
        ("cronos", "cro"),
        ("sui", "sui-network"),
        ("fantom", "ftm"),
    ],
)
def test_provider_network_ids_are_adapter_owned(chain_id: str, network: str) -> None:
    from ezchain_adapters.registry import get_chain

    meta = get_chain(chain_id)
    assert meta is not None
    assert meta.geckoterminal_id == network
