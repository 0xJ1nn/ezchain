"""GeckoTerminal OHLCV adapter — token price history.

Public API, no key. The documented free budget is ~30 requests/minute; the shared client
paces at 25/min (425 ms between requests) and caches one response for 30 seconds so a
page render does not spend the whole budget. A ``429`` backs off instead of retrying in a
storm: the shared client records the cooldown, and :meth:`GeckoTerminalClient.ohlcv`
sleeps a doubling delay (1 s, 2 s, 4 s) before asking again.

The response is **newest first** and includes the *forming* bar. This adapter returns
bars **oldest first** and leaves the forming bar in place: only the caller knows ``now``,
so only the caller can decide which bar is closed. The API service is the one place that
drops it (see ``apps/api/services/candles.py``).

``token`` is passed as our token's address so the OHLCV prices belong to the token we
asked about, not to the pool's base token. For a token quoted against USD this makes the
series directly comparable with ``price_usd`` elsewhere in the platform.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar
from urllib.parse import quote

import httpx

from ezchain_core import Candle

from .cache import BoundedTTLCache
from .errors import CandlesUnsupported, NotFound, RateLimited, UpstreamError
from .http import DEFAULT_TIMEOUT_SECONDS, Clock, JsonApiClient, Sleeper
from .ratelimit import TokenBucket, default_sleep
from .registry import ChainMeta, get_chain

BASE = "https://api.geckoterminal.com/api/v2"
WEB_BASE = "https://www.geckoterminal.com"
CACHE_TTL_SECONDS = 30.0
REQUESTS_PER_MINUTE = 25
BURST = 5
MAX_LIMIT = 1000
# A 429 is retried on these delays. The first is at least the shared client's default
# Retry-After clamp, so the recorded cooldown has expired before the next attempt.
RETRY_BACKOFF_SECONDS: tuple[float, ...] = (1.0, 2.0, 4.0)

# The six timeframes the platform exposes, mapped to (GeckoTerminal unit, aggregate).
TIMEFRAMES: dict[str, tuple[str, int]] = {
    "1m": ("minute", 1),
    "5m": ("minute", 5),
    "15m": ("minute", 15),
    "1h": ("hour", 1),
    "4h": ("hour", 4),
    "1d": ("day", 1),
}
UNIT_SECONDS: dict[str, int] = {"minute": 60, "hour": 3_600, "day": 86_400}


def timeframe_seconds(timeframe: str) -> int:
    """Bar length in seconds for one of :data:`TIMEFRAMES`; unknown labels fail closed."""
    try:
        unit, aggregate = TIMEFRAMES[timeframe]
    except KeyError as exc:
        raise CandlesUnsupported(f"unsupported timeframe: {timeframe}") from exc
    return UNIT_SECONDS[unit] * aggregate


def _dec(value: object) -> Decimal | None:
    """Parse a finite Decimal or return ``None``. Unknown is never zero."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _unix_seconds(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def parse_ohlcv(payload: object, timeframe_seconds_value: int) -> tuple[Candle, ...]:
    """Normalize one OHLCV response into bars, oldest first.

    A malformed row raises :class:`UpstreamError` instead of being skipped: a silently
    dropped bar would move an indicator (EMA/RSI/ATR) without telling anyone. Volume may
    be absent in the payload and is stored as ``None``; a missing price is an error.
    """
    if not isinstance(payload, dict):
        raise UpstreamError("GeckoTerminal returned an unexpected payload")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise UpstreamError("GeckoTerminal returned no data object")
    attributes = data.get("attributes")
    if not isinstance(attributes, dict):
        raise UpstreamError("GeckoTerminal returned no attributes")
    rows = attributes.get("ohlcv_list")
    if rows is None:
        return ()
    if not isinstance(rows, list):
        raise UpstreamError("GeckoTerminal returned a malformed ohlcv_list")

    bars: dict[datetime, Candle] = {}
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 6:
            raise UpstreamError("GeckoTerminal returned a malformed OHLCV row")
        opened = _unix_seconds(row[0])
        if opened is None:
            raise UpstreamError("GeckoTerminal returned a non-numeric open time")
        open_price = _dec(row[1])
        high = _dec(row[2])
        low = _dec(row[3])
        close = _dec(row[4])
        prices = (open_price, high, low, close)
        if any(price is None for price in prices):
            raise UpstreamError("GeckoTerminal returned an OHLCV row without a price")
        resolved = [price for price in prices if price is not None]
        if any(price <= 0 for price in resolved):
            raise UpstreamError("GeckoTerminal returned a non-positive OHLCV price")
        try:
            open_time = datetime.fromtimestamp(opened, tz=UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise UpstreamError("GeckoTerminal returned an out-of-range open time") from exc
        bars[open_time] = Candle(
            open_time=open_time,
            open=resolved[0],
            high=resolved[1],
            low=resolved[2],
            close=resolved[3],
            volume=_dec(row[5]),
            timeframe_seconds=timeframe_seconds_value,
        )

    return tuple(bars[key] for key in sorted(bars))


def pool_url(chain: ChainMeta, pool_address: str) -> str | None:
    """Human page for a pool, used as the series ``SourceRef.url``."""
    if chain.geckoterminal_id is None:
        return None
    return (
        f"{WEB_BASE}/{quote(chain.geckoterminal_id, safe='')}/pools/{quote(pool_address, safe='')}"
    )


class GeckoTerminalClient(JsonApiClient):
    label: ClassVar[str] = "GeckoTerminal"
    base_url: ClassVar[str] = BASE
    cache_ttl_seconds: ClassVar[float] = CACHE_TTL_SECONDS
    rate_per_second: ClassVar[float] = REQUESTS_PER_MINUTE / 60.0
    burst: ClassVar[int] = BURST

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        limiter: TokenBucket | None = None,
        cache: BoundedTTLCache | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleeper = default_sleep,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        retries: int = len(RETRY_BACKOFF_SECONDS),
    ) -> None:
        super().__init__(
            client, limiter=limiter, cache=cache, clock=clock, sleep=sleep, timeout=timeout
        )
        self._sleep = sleep
        self._retries = retries

    async def _get_with_backoff(self, path: str, params: dict[str, Any]) -> Any:
        """One GET, retried after a bounded backoff when the public budget is exhausted."""
        attempt = 0
        while True:
            try:
                return await self._get(path, params)
            except RateLimited:
                if attempt >= self._retries:
                    raise
                await self._sleep(RETRY_BACKOFF_SECONDS[attempt])
                attempt += 1

    async def _page(
        self,
        network: str,
        pool_address: str,
        token_address: str,
        timeframe: str,
        *,
        limit: int,
        before: datetime | int | None,
    ) -> tuple[Candle, ...]:
        unit, aggregate = TIMEFRAMES[timeframe]
        path = (
            f"/networks/{quote(network, safe='')}/pools/{quote(pool_address, safe='')}/ohlcv/{unit}"
        )
        params: dict[str, Any] = {
            "aggregate": aggregate,
            "limit": limit,
            "currency": "usd",
            "token": token_address,
        }
        if before is not None:
            params["before_timestamp"] = (
                int(before.timestamp()) if isinstance(before, datetime) else int(before)
            )
        payload = await self._get_with_backoff(path, params)
        return parse_ohlcv(payload, timeframe_seconds(timeframe))

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
        """Fetch up to ``limit`` bars, oldest first.

        ``before`` bounds the series at an exclusive open time (unix seconds or datetime)
        so a caller can walk backwards one page at a time. One call is one request: the
        1000-row cap is also the longest series the platform asks for, so paying for a
        second page here would only duplicate the service's fill logic.
        """
        meta = get_chain(chain_id)
        if meta is None:
            raise NotFound(f"unknown chain: {chain_id}")
        if meta.geckoterminal_id is None:
            raise CandlesUnsupported(f"{chain_id} has no GeckoTerminal OHLCV provider")
        if timeframe not in TIMEFRAMES:
            raise CandlesUnsupported(f"unsupported timeframe: {timeframe}")

        wanted = max(0, min(limit, MAX_LIMIT))
        if wanted == 0:
            return ()
        return await self._page(
            meta.geckoterminal_id,
            pool_address,
            token_address,
            timeframe,
            limit=wanted,
            before=before,
        )


__all__ = [
    "BASE",
    "MAX_LIMIT",
    "TIMEFRAMES",
    "GeckoTerminalClient",
    "parse_ohlcv",
    "pool_url",
    "timeframe_seconds",
]
