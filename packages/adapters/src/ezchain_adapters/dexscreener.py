"""DexScreener market data adapter.

Public API, no key. Rate limit: 300 requests/minute; the shared client paces at 5 req/s
(5 burst) and caches responses for 15 seconds. Every market row carries its source and
observation time.

The public API has no "top markets across chains" endpoint. Discovery therefore combines
the profile/boost/takeover feeds with per-chain seed tokens (:mod:`seeds`). For
``chain=all`` the client fans out one token lookup per registered chain instead of one
global batch: ``/latest/dex/tokens`` returns at most 30 *pairs* per response, so a shared
global batch under-reports the registry no matter how many addresses it contains.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from urllib.parse import quote

from ezchain_core import MarketRow, PairInfo, SourceRef, TokenSnapshot

from .errors import NotFound, UpstreamError
from .http import JsonApiClient
from .registry import CHAINS, ChainMeta, chain_for_dexscreener
from .seeds import SEED_TOKENS, seed_addresses

BASE = "https://api.dexscreener.com"
CACHE_TTL_SECONDS = 15.0
# Addresses per /latest/dex/tokens request path. The response is separately capped at 30
# pairs (see the module comment), which is why chain=all fans out per chain.
MAX_BATCH = 30
# Seeds first, then at most this many hint addresses per chain. A per-chain lookup stays
# inside one MAX_BATCH request in normal operation.
MAX_CHAIN_HINTS = 12
MAX_CONCURRENT_CHAINS = 4
# Safety cap for one ``tokens()`` call; it is not the API's response-pair cap.
MAX_DISCOVERY_ADDRESSES = MAX_BATCH * 4

SortMode = Literal["trending", "liquidity", "new"]
CoverageReason = Literal["pairs", "no_pairs", "error"]

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


def _known(value: Decimal | None) -> Decimal:
    """Sort sentinel only; the row keeps ``None`` so display can say unknown."""
    return Decimal(0) if value is None else value


def _rank(row: MarketRow) -> tuple[bool, Decimal, bool, Decimal]:
    """Deepest first, unknown liquidity/volume last. Never coerces the row itself."""
    return (
        row.liquidity_usd is not None,
        _known(row.liquidity_usd),
        row.volume_24h_usd is not None,
        _known(row.volume_24h_usd),
    )


def _path_segment(value: str) -> str:
    """Keep ``::`` addresses readable while escaping slashes in Cosmos denoms."""
    return quote(value, safe=":")


def normalize_pair(pair: object, observed: datetime | None = None) -> MarketRow | None:
    """Normalize one DexScreener pair into a market row.

    Rows whose chain is not registered or whose price/address is missing are dropped:
    showing a fabricated zero would violate the fail-closed rule. ``observed`` lets a
    caller stamp every row of one discovery with the same time as its ``SourceRef``.
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
        liquidity_usd=_field_dec(pair.get("liquidity"), "usd"),
        volume_24h_usd=_field_dec(pair.get("volume"), "h24"),
        price_change_m5=_field_dec(changes, "m5"),
        price_change_h1=_field_dec(changes, "h1"),
        price_change_h6=_field_dec(changes, "h6"),
        price_change_h24=_field_dec(changes, "h24"),
        pair_created_at=_dt_ms(pair.get("pairCreatedAt")),
        url=_optional_text(pair.get("url")),
        source=SourceRef("dexscreener", "https://dexscreener.com", observed or datetime.now(UTC)),
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


@dataclass(frozen=True)
class ChainCoverage:
    """What one chain contributed to a discovery pass."""

    chain: str
    requested: int
    returned: int
    reason: CoverageReason

    def to_dict(self) -> dict[str, object]:
        return {
            "chain": self.chain,
            "requested": self.requested,
            "returned": self.returned,
            "reason": self.reason,
        }


def chain_addresses(chain_id: str, hints: list[dict[str, Any]]) -> list[str]:
    """Seeds first, then at most MAX_CHAIN_HINTS registered-chain hint addresses.

    Hints are bucketed by chain, so an address shared with an unregistered chain (WETH on
    PulseChain/Ink/Soneium/World Chain) cannot displace another registered chain's lookup.
    """
    addresses: list[str] = []
    seen: set[str] = set()

    def add(value: object) -> bool:
        if not isinstance(value, str) or not value:
            return False
        key = value.lower()
        if key in seen:
            return False
        seen.add(key)
        addresses.append(value)
        return True

    for address in seed_addresses(chain_id):
        add(address)
    hint_count = 0
    for item in hints:
        if hint_count >= MAX_CHAIN_HINTS:
            break
        hint_chain = chain_for_dexscreener(item.get("chainId") or "")
        if hint_chain is None or hint_chain.id != chain_id:
            continue
        if add(item.get("tokenAddress")):
            hint_count += 1
    return addresses


def _sort_rows(rows: list[MarketRow], sort: SortMode) -> list[MarketRow]:
    """Sort within one chain. Unknown values sort last and stay ``None`` on the row."""
    if sort == "new":
        return sorted(
            rows,
            key=lambda row: (
                (row.pair_created_at or row.source.observed_at).timestamp(),
                row.liquidity_usd is not None,
                _known(row.liquidity_usd),
            ),
            reverse=True,
        )
    if sort == "liquidity":
        return sorted(rows, key=_rank, reverse=True)
    return sorted(
        rows,
        key=lambda row: (
            row.volume_24h_usd is not None,
            _known(row.volume_24h_usd),
            row.liquidity_usd is not None,
            _known(row.liquidity_usd),
        ),
        reverse=True,
    )


def _dedupe_rows(raw: list[dict[str, Any]], chain_id: str, observed: datetime) -> list[MarketRow]:
    deduped: dict[str, MarketRow] = {}
    for pair in raw:
        row = normalize_pair(pair, observed)
        if row is None or row.chain != chain_id:
            continue
        current = deduped.get(row.pair_address)
        if current is None or _rank(row) > _rank(current):
            deduped[row.pair_address] = row
    return list(deduped.values())


def _shared_seed_addresses() -> set[str]:
    """Seed addresses claimed by more than one registered chain (e.g. Base/Optimism WETH)."""
    counts: dict[str, int] = {}
    for addresses in SEED_TOKENS.values():
        for address in addresses:
            key = address.lower()
            counts[key] = counts.get(key, 0) + 1
    return {address for address, count in counts.items() if count > 1}


def _round_robin(groups: list[list[MarketRow]]) -> list[MarketRow]:
    """Interleave already-sorted chain groups in registry order, one row at a time."""
    merged: list[MarketRow] = []
    depth = 0
    while True:
        added = False
        for group in groups:
            if depth < len(group):
                merged.append(group[depth])
                added = True
        if not added:
            return merged
        depth += 1


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
        """Fetch pairs for addresses, ``MAX_BATCH`` per request path.

        The API caps each response at 30 pairs; adding addresses does not raise that cap.
        ``chain=all`` therefore calls this once per chain rather than once for the whole
        registry.
        """
        pairs: list[dict[str, Any]] = []
        for start in range(0, min(len(addresses), MAX_DISCOVERY_ADDRESSES), MAX_BATCH):
            batch = addresses[start : start + MAX_BATCH]
            path = "/latest/dex/tokens/" + ",".join(_path_segment(item) for item in batch)
            pairs.extend(_pairs_payload(await self._get(path)))
        return pairs

    async def search(self, query: str) -> list[dict[str, Any]]:
        return _pairs_payload(await self._get("/latest/dex/search", {"q": query}))

    async def _hint_items(self) -> list[dict[str, Any]]:
        results = await asyncio.gather(
            self.latest_profiles(),
            self.latest_boosts(),
            self.top_boosts(),
            self.latest_takeovers(),
            return_exceptions=True,
        )
        items: list[dict[str, Any]] = []
        for result in results:
            if isinstance(result, BaseException):
                continue
            items.extend(result)
        return items

    async def _rows_for_addresses(
        self, chain_id: str, addresses: list[str], observed: datetime, sort: SortMode
    ) -> tuple[list[MarketRow], bool]:
        """Return (rows, failed). ``failed`` means the chain probe itself raised.

        ``/latest/dex/tokens`` caps the response at 30 pairs. When a shared address (for
        example WETH on Base and Optimism) fills that cap with pairs on other chains, the
        chain would look empty even though it has data. For a shared seed only, fall back
        to the chain-filtered endpoint. Chains with unique seeds do not pay for a second
        request when they genuinely have no pairs.
        """
        if not addresses:
            return [], False
        raw = await self.tokens(addresses)
        rows = _sort_rows(_dedupe_rows(raw, chain_id, observed), sort)
        if rows:
            return rows, False
        shared = _shared_seed_addresses()
        for address in (item for item in addresses if item.lower() in shared):
            try:
                raw = await self.token_pairs(chain_id, address)
            except Exception:  # cannot distinguish empty from failed after a cap
                return [], True
            rows = _sort_rows(_dedupe_rows(raw, chain_id, observed), sort)
            if rows:
                return rows, False
        return [], False

    async def _discover_all(
        self, observed: datetime, sort: SortMode
    ) -> tuple[list[MarketRow], list[ChainCoverage]]:
        """One token lookup per registered chain, bounded to four in flight.

        A chain with no addresses is reported as ``no_pairs`` rather than being invented.
        A failing chain yields no rows and `no_pairs`; it never aborts the other chains.
        """
        hints = await self._hint_items()
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHAINS)

        async def probe(meta: ChainMeta) -> tuple[str, list[MarketRow], int, bool]:
            addresses = chain_addresses(meta.id, hints)
            if not addresses:
                return meta.id, [], 0, False
            try:
                async with semaphore:
                    rows, failed = await self._rows_for_addresses(
                        meta.id, addresses, observed, sort
                    )
            except Exception:  # one chain/provider failure must not stop discovery
                rows, failed = [], True
            return meta.id, rows, len(addresses), failed

        results = await asyncio.gather(*(probe(chain) for chain in CHAINS))
        coverage = [
            ChainCoverage(
                chain,
                requested,
                len(rows),
                "error" if failed else ("pairs" if rows else "no_pairs"),
            )
            for chain, rows, requested, failed in results
        ]
        return _round_robin([rows for _, rows, _, _ in results]), coverage

    async def _discover_one(
        self, chain: str, observed: datetime, sort: SortMode
    ) -> tuple[list[MarketRow], list[ChainCoverage]]:
        hints = await self._hint_items()
        addresses = chain_addresses(chain, hints)
        try:
            rows, failed = await self._rows_for_addresses(chain, addresses, observed, sort)
        except Exception:
            rows, failed = [], True
        coverage = [
            ChainCoverage(
                chain,
                len(addresses),
                len(rows),
                "error" if failed else ("pairs" if rows else "no_pairs"),
            )
        ]
        return rows, coverage

    async def discover_markets(
        self,
        *,
        chain: str | None = None,
        query: str | None = None,
        sort: SortMode = "trending",
        limit: int = 50,
    ) -> tuple[list[MarketRow], SourceRef, list[ChainCoverage] | None]:
        """Discover rows plus per-chain coverage.

        Search is a single global query and deliberately returns ``coverage=None``: it is
        not an all-chain census. Discovery for ``chain=all`` interleaves registered chains
        before slicing so a low-liquidity chain is not dropped by a global ranking.
        """
        observed = datetime.now(UTC)
        source = SourceRef("dexscreener", "https://dexscreener.com", observed)
        if limit <= 0:
            return [], source, None

        if query:
            raw = await self.search(query)
            # Search may span chains, so normalize once and filter by the requested chain.
            matched = [
                row for row in (normalize_pair(pair, observed) for pair in raw) if row is not None
            ]
            if chain and chain != "all":
                matched = [row for row in matched if row.chain == chain]
            deduped: dict[str, MarketRow] = {}
            for row in matched:
                key = f"{row.chain}:{row.pair_address}"
                current = deduped.get(key)
                if current is None or _rank(row) > _rank(current):
                    deduped[key] = row
            ordered = _sort_rows(list(deduped.values()), sort)
            return ordered[:limit], source, None

        if chain and chain != "all":
            rows, coverage = await self._discover_one(chain, observed, sort)
            return rows[:limit], source, coverage

        rows, coverage = await self._discover_all(observed, sort)
        return rows[:limit], source, coverage

    async def list_markets(
        self,
        *,
        chain: str | None = None,
        query: str | None = None,
        sort: SortMode = "trending",
        limit: int = 50,
    ) -> tuple[list[MarketRow], SourceRef]:
        rows, source, _coverage = await self.discover_markets(
            chain=chain, query=query, sort=sort, limit=limit
        )
        return rows, source

    async def get_token(self, chain: str, address: str) -> TokenSnapshot:
        meta = chain_for_dexscreener(chain)
        if meta is None:
            raise NotFound(f"unknown chain: {chain}")
        pairs = await self.token_pairs(chain, address)
        return snapshot_from_pairs(meta, address, pairs)
