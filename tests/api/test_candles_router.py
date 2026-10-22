"""Candles router: 200 shape, reason-coded errors, fixture labelling.

The provider and the market source are stubs; the session is the real SQLite schema.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from ezchain_adapters import UpstreamError
from ezchain_api.config import Settings, get_settings
from ezchain_api.deps import get_dexscreener, get_geckoterminal
from ezchain_api.routers import markets
from ezchain_core import Candle, PairInfo, SourceRef, TokenSnapshot

CHAIN = "ethereum"
TOKEN = "0xtoken"
POOL = "0xpool"
NOW = datetime(2026, 10, 22, 12, 30, tzinfo=UTC)
FIVE_MINUTES = 300


def bar(open_time: datetime, close: str, volume: str | None) -> Candle:
    price = Decimal(close)
    return Candle(
        open_time=open_time,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=None if volume is None else Decimal(volume),
        timeframe_seconds=FIVE_MINUTES,
    )


def recent_bars() -> tuple[Candle, ...]:
    """Two closed 5m bars ending at the latest closed bucket before the real ``now``.

    The router stamps its own ``now``, so a fixed timestamp would make a fresh series look
    stale. The prices stay fixed; only the open times move with the clock.
    """
    stamp = int(datetime.now(UTC).timestamp())
    latest = stamp - stamp % FIVE_MINUTES - FIVE_MINUTES
    return (
        bar(datetime.fromtimestamp(latest - FIVE_MINUTES, tz=UTC), "1.10000000", "10.5"),
        bar(datetime.fromtimestamp(latest, tz=UTC), "1.20000000", None),
    )


class FakeCandles:
    def __init__(
        self, candles: tuple[Candle, ...] | None = None, error: Exception | None = None
    ) -> None:
        self.candles = candles
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def ohlcv(
        self,
        chain_id: str,
        pool_address: str,
        token_address: str,
        timeframe: str,
        *,
        limit: int = 300,
        before: datetime | int | None = None,
    ) -> tuple[Candle, ...]:
        self.calls.append({"chain": chain_id, "timeframe": timeframe, "limit": limit})
        if self.error is not None:
            raise self.error
        return self.candles if self.candles is not None else recent_bars()


class FakeSnapshots:
    async def get_token(self, chain: str, address: str) -> TokenSnapshot:
        return TokenSnapshot(
            chain=chain,
            address=address,
            symbol="TKN",
            name="Token",
            price_usd=Decimal("1.2"),
            liquidity_usd=Decimal("1000"),
            volume_24h_usd=None,
            price_change_m5=None,
            price_change_h1=None,
            price_change_h6=None,
            price_change_h24=None,
            pairs=(
                PairInfo(
                    pair_address=POOL,
                    dex="testdex",
                    quote_token="USDC",
                    price_usd=Decimal("1.2"),
                    liquidity_usd=Decimal("1000"),
                    volume_24h_usd=None,
                    url=None,
                ),
            ),
            source=SourceRef("dexscreener", "https://dexscreener.com", NOW),
            stale=False,
        )


class ExplodingCandles:
    async def ohlcv(self, *args: Any, **kwargs: Any) -> tuple[Candle, ...]:
        raise AssertionError("fixture mode must not reach the candle provider")


def live_settings() -> Settings:
    return Settings(
        environment="test",
        data_mode="live",
        database_url="sqlite+pysqlite://",
        auto_create_schema=False,
    )


def build(
    client_for: Callable[..., TestClient],
    *,
    candles: Any = None,
    settings: Settings | None = None,
) -> TestClient:
    overrides: dict[Any, Any] = {
        get_dexscreener: lambda: FakeSnapshots(),
        get_geckoterminal: lambda: candles or FakeCandles(),
        get_settings: lambda: settings or live_settings(),
    }
    router: APIRouter = markets.router
    return client_for(router, overrides)


def test_candles_are_returned_oldest_first_as_strings(
    client_for: Callable[..., TestClient],
) -> None:
    client = build(client_for)
    response = client.get(f"/api/v1/tokens/{CHAIN}/{TOKEN}/candles?timeframe=5m&limit=2")

    assert response.status_code == 200
    body = response.json()
    assert body["chain"] == CHAIN
    assert body["token_address"] == TOKEN
    assert body["timeframe"] == "5m"
    assert body["pool_address"] == POOL
    assert body["stale"] is False
    assert body["reason"] is None
    assert body["source"]["name"] == "geckoterminal"
    assert body["source"]["url"] == f"https://www.geckoterminal.com/eth/pools/{POOL}"
    expected = recent_bars()
    assert [row["open_time"] for row in body["candles"]] == [
        candle.open_time.isoformat() for candle in expected
    ]
    assert [row["open"] for row in body["candles"]] == ["1.10000000", "1.20000000"]
    # Unknown volume is null on the wire, never "0".
    assert [row["volume"] for row in body["candles"]] == ["10.5", None]
    assert body["candles"][0]["close_time"] == expected[0].close_time.isoformat()


def test_unknown_chain_is_404_chain_not_found(client_for: Callable[..., TestClient]) -> None:
    client = build(client_for)
    response = client.get("/api/v1/tokens/nope/0xtoken/candles")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "CHAIN_NOT_FOUND"


def test_registered_chain_without_a_provider_is_404_unsupported(
    client_for: Callable[..., TestClient],
) -> None:
    client = build(client_for)
    response = client.get("/api/v1/tokens/osmosis/0xtoken/candles")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "CANDLES_UNSUPPORTED_CHAIN"


def test_upstream_failure_is_502_upstream_unreachable(
    client_for: Callable[..., TestClient],
) -> None:
    client = build(client_for, candles=FakeCandles(error=UpstreamError("provider down")))
    response = client.get(f"/api/v1/tokens/{CHAIN}/{TOKEN}/candles")

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["code"] == "UPSTREAM_UNREACHABLE"
    assert detail["retryable"] is True


def test_unknown_timeframe_is_rejected_before_the_provider(
    client_for: Callable[..., TestClient],
) -> None:
    candles = FakeCandles()
    client = build(client_for, candles=candles)
    response = client.get(f"/api/v1/tokens/{CHAIN}/{TOKEN}/candles?timeframe=7m")

    assert response.status_code == 422
    assert candles.calls == []


@pytest.mark.parametrize("limit", [0, -1, 1001])
def test_limit_bounds_are_enforced(
    client_for: Callable[..., TestClient], limit: int
) -> None:
    client = build(client_for)
    response = client.get(f"/api/v1/tokens/{CHAIN}/{TOKEN}/candles?limit={limit}")

    assert response.status_code == 422


def test_fixture_mode_is_labelled_and_offline(client_for: Callable[..., TestClient]) -> None:
    fixture = Settings(
        environment="test",
        data_mode="fixture",
        database_url="sqlite+pysqlite://",
        auto_create_schema=False,
    )
    client = build(client_for, candles=ExplodingCandles(), settings=fixture)
    response = client.get(f"/api/v1/tokens/{CHAIN}/{TOKEN}/candles?timeframe=1h&limit=3")

    assert response.status_code == 200
    body = response.json()
    assert body["source"]["name"] == "fixture"
    assert body["source"]["url"] is None
    assert len(body["candles"]) == 3
    opens = [row["open_time"] for row in body["candles"]]
    assert opens == sorted(opens)


def test_candles_are_not_registered_under_the_token_route(
    client_for: Callable[..., TestClient],
) -> None:
    """The candles path must be its own route, not a query on the token detail."""
    client = build(client_for)
    response = client.get(f"/api/v1/tokens/{CHAIN}/{TOKEN}")

    assert response.status_code == 200
    assert "candles" not in response.json()


def test_bar_times_stay_on_the_timeframe_grid(client_for: Callable[..., TestClient]) -> None:
    client = build(client_for)
    body = client.get(f"/api/v1/tokens/{CHAIN}/{TOKEN}/candles?timeframe=5m&limit=2").json()

    now = datetime.now(UTC)
    for row in body["candles"]:
        opened = datetime.fromisoformat(row["open_time"])
        closed = datetime.fromisoformat(row["close_time"])
        assert (closed - opened) == timedelta(seconds=FIVE_MINUTES)
        assert opened.utcoffset() == timedelta(0)
        assert opened.minute % 5 == 0 and opened.second == 0
        assert opened <= now
