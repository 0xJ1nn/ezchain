"""DexScreener market data adapter.

Public API, no key. Rate limit: 300 requests/minute; this client stays at 5 req/s and
caches responses for 15 seconds. Every market row carries the source and observation time.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from ezchain_core import MarketRow, PairInfo, SourceRef, TokenSnapshot

from .errors import NotFound, RateLimited, UpstreamError
from .registry import ChainMeta, chain_for_dexscreener

BASE = "https://api.dexscreener.com"
CACHE_TTL_SECONDS = 15.0
MAX_BATCH = 30


class _RateLimiter:
    def __init__(self, rate_per_second: float = 5.0, burst: int = 5) -> None:
        self.rate = rate_per_second
        self.burst = burst
        self._times: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            while self._times and now - self._times[0] > 1.0:
                self._times.popleft()
            if len(self._times) >= self.burst:
                wait = 1.0 - (now - self._times[0])
                if wait > 0:
                    await asyncio.sleep(wait)
            self._times.append(time.monotonic())


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _dt_ms(value: Any) -> datetime | None:
    if not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def normalize_pair(pair: dict[str, Any]) -> MarketRow | None:
    chain_meta = chain_for_dexscreener(str(pair.get("chainId", "")))
    if chain_meta is None:
        return None
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    price = _dec(pair.get("priceUsd"))
    if price is None:
        return None
    liquidity = _dec((pair.get("liquidity") or {}).get("usd")) or Decimal(0)
    volume = _dec((pair.get("volume") or {}).get("h24")) or Decimal(0)
    changes = pair.get("priceChange") or {}
    return MarketRow(
        chain=chain_meta.id,
        pair_address=str(pair.get("pairAddress", "")),
        dex=str(pair.get("dexId", "unknown")),
        base_token=str(base.get("address", "")),
        quote_token=str(quote.get("address", "")),
        symbol=str(base.get("symbol", "?")),
        name=str(base.get("name", base.get("symbol", "Unknown"))),
        price_usd=price,
        liquidity_usd=liquidity,
        volume_24h_usd=volume,
        price_change_m5=_dec(changes.get("m5")),
        price_change_h1=_dec(changes.get("h1")),
        price_change_h6=_dec(changes.get("h6")),
        price_change_h24=_dec(changes.get("h24")),
        pair_created_at=_dt_ms(pair.get("pairCreatedAt")),
        url=pair.get("url"),
        source=SourceRef("dexscreener", "https://dexscreener.com", datetime.now(UTC)),
    )


def snapshot_from_pairs(
    chain: ChainMeta, address: str, pairs: list[dict[str, Any]]
) -> TokenSnapshot:
    rows = [r for r in (normalize_pair(p) for p in pairs) if r is not None]
    if not rows:
        raise NotFound(f"no pairs for {chain.id}:{address}")
    best = max(rows, key=lambda r: (r.liquidity_usd, r.volume_24h_usd))
    pair_infos = tuple(
        PairInfo(
            pair_address=r.pair_address,
            dex=r.dex,
            quote_token=r.quote_token,
            price_usd=r.price_usd,
            liquidity_usd=r.liquidity_usd,
            volume_24h_usd=r.volume_24h_usd,
            url=r.url,
        )
        for r in rows
    )
    return TokenSnapshot(
        chain=chain.id,
        address=address,
        symbol=best.symbol,
        name=best.name,
        price_usd=best.price_usd,
        liquidity_usd=best.liquidity_usd,
        volume_24h_usd=best.volume_24h_usd,
        price_change_m5=best.price_change_m5,
        price_change_h1=best.price_change_h1,
        price_change_h6=best.price_change_h6,
        price_change_h24=best.price_change_h24,
        pairs=pair_infos,
        safety={"status": "unknown", "reason": "SAFETY_NOT_IMPLEMENTED"},
        source=best.source,
        stale=False,
    )


class DexScreenerClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None
        self._limiter = _RateLimiter()
        self._cache: dict[str, tuple[float, Any]] = {}
        self._cache_lock = asyncio.Lock()

    async def __aenter__(self) -> DexScreenerClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=BASE,
                timeout=httpx.Timeout(15.0),
                headers={"User-Agent": "ezCHAIN/0.1 (+https://github.com/0xJ1nn/ezchain)"},
            )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        key = f"{path}?{sorted((params or {}).items())}"
        now = time.monotonic()
        async with self._cache_lock:
            hit = self._cache.get(key)
            if hit and hit[0] > now:
                return hit[1]
        await self._limiter.acquire()
        assert self._client is not None
        try:
            response = await self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise UpstreamError(f"DexScreener request failed: {exc}") from exc
        if response.status_code == 429:
            raise RateLimited("DexScreener rate limit")
        if response.status_code >= 400:
            raise UpstreamError(f"DexScreener returned {response.status_code}")
        data = response.json()
        async with self._cache_lock:
            self._cache[key] = (time.monotonic() + CACHE_TTL_SECONDS, data)
        return data

    async def latest_profiles(self) -> list[dict[str, Any]]:
        data = await self._get("/token-profiles/latest/v1")
        return list(data) if isinstance(data, list) else []

    async def latest_boosts(self) -> list[dict[str, Any]]:
        data = await self._get("/token-boosts/latest/v1")
        return list(data) if isinstance(data, list) else []

    async def top_boosts(self) -> list[dict[str, Any]]:
        data = await self._get("/token-boosts/top/v1")
        return list(data) if isinstance(data, list) else []

    async def latest_takeovers(self) -> list[dict[str, Any]]:
        data = await self._get("/community-takeovers/latest/v1")
        return list(data) if isinstance(data, list) else []

    async def token_pairs(self, chain: str, address: str) -> list[dict[str, Any]]:
        data = await self._get(f"/token-pairs/v1/{chain}/{address}")
        return list(data) if isinstance(data, list) else []

    async def tokens(self, addresses: list[str]) -> list[dict[str, Any]]:
        pairs: list[dict[str, Any]] = []
        for start in range(0, len(addresses), MAX_BATCH):
            batch = addresses[start : start + MAX_BATCH]
            data = await self._get(f"/latest/dex/tokens/{','.join(batch)}")
            pairs.extend(data.get("pairs") or [])
        return pairs

    async def search(self, query: str) -> list[dict[str, Any]]:
        data = await self._get("/latest/dex/search", {"q": query})
        return list(data.get("pairs") or [])

    async def list_markets(
        self, *, chain: str | None = None, query: str | None = None, limit: int = 50
    ) -> tuple[list[MarketRow], SourceRef]:
        observed = datetime.now(UTC)
        if query:
            raw = await self.search(query)
        else:
            hints = await asyncio.gather(
                self.latest_profiles(),
                self.latest_boosts(),
                self.top_boosts(),
                self.latest_takeovers(),
                return_exceptions=True,
            )
            addresses: list[str] = []
            for result in hints:
                if isinstance(result, BaseException):
                    continue
                for item in result:
                    address = item.get("tokenAddress")
                    item_chain = item.get("chainId")
                    if not address:
                        continue
                    if chain and chain != "all" and item_chain != chain:
                        continue
                    if address not in addresses:
                        addresses.append(address)
            raw = await self.tokens(addresses[: MAX_BATCH * 2]) if addresses else []
        rows = [r for r in (normalize_pair(p) for p in raw) if r is not None]
        if chain and chain != "all":
            rows = [r for r in rows if r.chain == chain]
        deduped: dict[str, MarketRow] = {}
        for row in rows:
            key = f"{row.chain}:{row.pair_address}"
            current = deduped.get(key)
            if current is None or row.liquidity_usd > current.liquidity_usd:
                deduped[key] = row
        ordered = sorted(
            deduped.values(), key=lambda r: (r.liquidity_usd, r.volume_24h_usd), reverse=True
        )
        return ordered[:limit], SourceRef("dexscreener", "https://dexscreener.com", observed)

    async def get_token(self, chain: str, address: str) -> TokenSnapshot:
        meta = chain_for_dexscreener(chain)
        if meta is None:
            raise NotFound(f"unknown chain: {chain}")
        pairs = await self.token_pairs(chain, address)
        return snapshot_from_pairs(meta, address, pairs)
