"""DexScreener market data adapter.

Public API, no key. Rate limit: 300 requests/minute; the shared client paces at 5 req/s
(5 burst) and caches responses for 15 seconds. Every market row carries its source and
observation time.

The public API has no "top markets across chains" endpoint. Discovery therefore combines
the profile/boost/takeover feeds with per-chain seed tokens (:mod:`seeds`), so browsing
``chain=all`` is not a Solana-only board.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

from ezchain_core import MarketRow, PairInfo, SourceRef, TokenSnapshot

from .errors import NotFound, UpstreamError
from .http import JsonApiClient
from .registry import ChainMeta, chain_for_dexscreener
from .seeds import seed_addresses

BASE = "https://api.dexscreener.com"
CACHE_TTL_SECONDS = 15.0
MAX_BATCH = 30
# Four batches of 30 = 120 addresses. With the four hint endpoints that is at most eight
# requests per discovery, comfortably inside the 300/minute budget at 5 req/s.
MAX_DISCOVERY_ADDRESSES = MAX_BATCH * 4

SAFETY_UNKNOWN = {"status": "unknown", "reason": "SAFETY_NOT_IMPLEMENTED"}


def _dec(value: object) -> Decimal | None:
    """Parse a finite Decimal or return ``None``. Unknown is never zero."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _field_dec(container: object, key: str) -> Decimal | None:
    if isinstance(container, dict):
        return _dec(container.get(key))
    return None


def _text(value: object, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value
    return default


def _optional_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _dt_ms(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _rank(row: MarketRow) -> tuple[Decimal, Decimal]:
    return (row.liquidity_usd, row.volume_24h_usd)


def _path_segment(value: str) -> str:
    """Keep ``::`` addresses readable while escaping slashes in Cosmos denoms."""
    return quote(value, safe=":")


def normalize_pair(pair: object) -> MarketRow | None:
    """Normalize one DexScreener pair into a market row.

    Rows whose chain is not registered or whose price/address is missing are dropped:
    showing a fabricated zero would violate the fail-closed rule.
    """
    if not isinstance(pair, dict):
        return None
    chain_meta = chain_for_dexscreener(_text(pair.get("chainId"), ""))
    if chain_meta is None:
        return None
    pair_address = _text(pair.get("pairAddress"), "")
    if not pair_address:
        return None
    price = _dec(pair.get("priceUsd"))
    if price is None or price <= 0:
        return None

    base = pair.get("baseToken")
    quote_token = pair.get("quoteToken")
    base_dict = base if isinstance(base, dict) else {}
    quote_dict = quote_token if isinstance(quote_token, dict) else {}
    changes = pair.get("priceChange")
    symbol = _text(base_dict.get("symbol"), "?")
    return MarketRow(
        chain=chain_meta.id,
        pair_address=pair_address,
        dex=_text(pair.get("dexId"), "unknown"),
        base_token=_text(base_dict.get("address"), ""),
        quote_token=_text(quote_dict.get("address"), ""),
        symbol=symbol,
        name=_text(base_dict.get("name"), symbol),
        price_usd=price,
        liquidity_usd=_field_dec(pair.get("liquidity"), "usd") or Decimal(0),
        volume_24h_usd=_field_dec(pair.get("volume"), "h24") or Decimal(0),
        price_change_m5=_field_dec(changes, "m5"),
        price_change_h1=_field_dec(changes, "h1"),
        price_change_h6=_field_dec(changes, "h6"),
        price_change_h24=_field_dec(changes, "h24"),
        pair_created_at=_dt_ms(pair.get("pairCreatedAt")),
        url=_optional_text(pair.get("url")),
        source=SourceRef("dexscreener", "https://dexscreener.com", datetime.now(UTC)),
        quote_symbol=_text(quote_dict.get("symbol"), "?"),
        quote_name=_text(quote_dict.get("name"), _text(quote_dict.get("symbol"), "Unknown")),
        price_native=_dec(pair.get("priceNative")),
    )


def _resolve_side(
    row: MarketRow, address: str
) -> (
    tuple[str, str, Decimal, Decimal | None, Decimal | None, Decimal | None, Decimal | None] | None
):
    """Resolve the requested token's symbol, name, price and changes from a pair.

    DexScreener's ``priceUsd`` is for the base token. When the requested token is the
    quote side, derive its USD price from ``priceNative`` (quote per base) as
    ``priceUsd / priceNative``. If that field is missing, fail closed instead of showing
    the base token's symbol or a guessed stablecoin price.
    """
    if row.base_token == address:
        return (
            row.symbol,
            row.name,
            row.price_usd,
            row.price_change_m5,
            row.price_change_h1,
            row.price_change_h6,
            row.price_change_h24,
        )
    if row.quote_token == address:
        native = row.price_native
        if native is None or native <= 0:
            return None
        return (
            row.quote_symbol,
            row.quote_name,
            row.price_usd / native,
            None,
            None,
            None,
            None,
        )
    return None


def snapshot_from_pairs(
    chain: ChainMeta, address: str, pairs: list[dict[str, Any]]
) -> TokenSnapshot:
    """Build a token snapshot for the requested side of its deepest pair."""
    candidates: list[
        tuple[
            MarketRow,
            tuple[
                str,
                str,
                Decimal,
                Decimal | None,
                Decimal | None,
                Decimal | None,
                Decimal | None,
            ],
        ]
    ] = []
    quote_fallback: list[
        tuple[
            MarketRow,
            tuple[
                str,
                str,
                Decimal,
                Decimal | None,
                Decimal | None,
                Decimal | None,
                Decimal | None,
            ],
        ]
    ] = []
    for pair in pairs:
        row = normalize_pair(pair)
        if row is None or row.chain != chain.id:
            continue
        resolved = _resolve_side(row, address)
        if resolved is None:
            continue
        target = candidates if row.base_token == address else quote_fallback
        target.append((row, resolved))
    selected = candidates or quote_fallback
    if not selected:
        raise NotFound(f"no priced pair for {chain.id}:{address}")

    unique: dict[str, tuple[MarketRow, tuple[Any, ...]]] = {}
    for row, resolved in selected:
        current = unique.get(row.pair_address)
        if current is None or _rank(row) > _rank(current[0]):
            unique[row.pair_address] = (row, resolved)
    ranked = sorted(unique.values(), key=lambda item: _rank(item[0]), reverse=True)
    best_row, best = ranked[0]
    pair_infos = tuple(
        PairInfo(
            pair_address=row.pair_address,
            dex=row.dex,
            quote_token=row.quote_token,
            price_usd=resolved[2],
            liquidity_usd=row.liquidity_usd,
            volume_24h_usd=row.volume_24h_usd,
            url=row.url,
        )
        for row, resolved in ranked
    )
    return TokenSnapshot(
        chain=chain.id,
        address=address,
        symbol=best[0],
        name=best[1],
        price_usd=best[2],
        liquidity_usd=best_row.liquidity_usd,
        volume_24h_usd=best_row.volume_24h_usd,
        price_change_m5=best[3],
        price_change_h1=best[4],
        price_change_h6=best[5],
        price_change_h24=best[6],
        pairs=pair_infos,
        safety=dict(SAFETY_UNKNOWN),
        source=best_row.source,
        stale=False,
    )


def _pairs_payload(data: object) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        raise UpstreamError("DexScreener returned an unexpected payload")
    pairs = data.get("pairs")
    if pairs is None:
        return []
    if not isinstance(pairs, list):
        raise UpstreamError("DexScreener returned a malformed pairs payload")
    return [pair for pair in pairs if isinstance(pair, dict)]


def _list_payload(data: object) -> list[dict[str, Any]]:
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _pair_list_payload(data: object) -> list[dict[str, Any]]:
    if not isinstance(data, list):
        raise UpstreamError("DexScreener returned an unexpected pairs payload")
    return [item for item in data if isinstance(item, dict)]


class DexScreenerClient(JsonApiClient):
    label = "DexScreener"
    base_url = BASE
    cache_ttl_seconds = CACHE_TTL_SECONDS
    rate_per_second = 5.0  # 300 requests/minute, the documented ceiling
    burst = 5

    async def latest_profiles(self) -> list[dict[str, Any]]:
        return _list_payload(await self._get("/token-profiles/latest/v1"))

    async def latest_boosts(self) -> list[dict[str, Any]]:
        return _list_payload(await self._get("/token-boosts/latest/v1"))

    async def top_boosts(self) -> list[dict[str, Any]]:
        return _list_payload(await self._get("/token-boosts/top/v1"))

    async def latest_takeovers(self) -> list[dict[str, Any]]:
        return _list_payload(await self._get("/community-takeovers/latest/v1"))

    async def token_pairs(self, chain: str, address: str) -> list[dict[str, Any]]:
        path = f"/token-pairs/v1/{_path_segment(chain)}/{_path_segment(address)}"
        return _pair_list_payload(await self._get(path))

    async def tokens(self, addresses: list[str]) -> list[dict[str, Any]]:
        """Fetch pairs for up to ``MAX_DISCOVERY_ADDRESSES`` tokens, 30 per request."""
        pairs: list[dict[str, Any]] = []
        for start in range(0, min(len(addresses), MAX_DISCOVERY_ADDRESSES), MAX_BATCH):
            batch = addresses[start : start + MAX_BATCH]
            path = "/latest/dex/tokens/" + ",".join(_path_segment(item) for item in batch)
            pairs.extend(_pairs_payload(await self._get(path)))
        return pairs

    async def search(self, query: str) -> list[dict[str, Any]]:
        return _pairs_payload(await self._get("/latest/dex/search", {"q": query}))

    async def _discover(self, chain: str | None) -> list[dict[str, Any]]:
        hints = await asyncio.gather(
            self.latest_profiles(),
            self.latest_boosts(),
            self.top_boosts(),
            self.latest_takeovers(),
            return_exceptions=True,
        )
        addresses: list[str] = []
        seen: set[str] = set()

        def add(address: object) -> None:
            if not isinstance(address, str) or not address or address in seen:
                return
            seen.add(address)
            addresses.append(address)

        # Seeds first so a flood of Solana hint addresses cannot starve other chains.
        for address in seed_addresses(chain):
            add(address)
        for result in hints:
            if isinstance(result, BaseException):
                continue
            for item in result:
                item_chain = item.get("chainId")
                if not isinstance(item_chain, str):
                    continue
                if chain and chain != "all" and item_chain != chain:
                    continue
                if (not chain or chain == "all") and chain_for_dexscreener(item_chain) is None:
                    continue
                add(item.get("tokenAddress"))
        if not addresses:
            return []
        return await self.tokens(addresses[:MAX_DISCOVERY_ADDRESSES])

    async def list_markets(
        self, *, chain: str | None = None, query: str | None = None, limit: int = 50
    ) -> tuple[list[MarketRow], SourceRef]:
        observed = datetime.now(UTC)
        source = SourceRef("dexscreener", "https://dexscreener.com", observed)
        if limit <= 0:
            return [], source
        raw = await self.search(query) if query else await self._discover(chain)
        rows = [row for row in (normalize_pair(pair) for pair in raw) if row is not None]
        if chain and chain != "all":
            rows = [row for row in rows if row.chain == chain]
        deduped: dict[str, MarketRow] = {}
        for row in rows:
            key = f"{row.chain}:{row.pair_address}"
            current = deduped.get(key)
            if current is None or _rank(row) > _rank(current):
                deduped[key] = row
        ordered = sorted(deduped.values(), key=_rank, reverse=True)
        return ordered[:limit], source

    async def get_token(self, chain: str, address: str) -> TokenSnapshot:
        meta = chain_for_dexscreener(chain)
        if meta is None:
            raise NotFound(f"unknown chain: {chain}")
        pairs = await self.token_pairs(chain, address)
        return snapshot_from_pairs(meta, address, pairs)
