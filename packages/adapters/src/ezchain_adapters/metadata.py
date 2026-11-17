"""Token metadata normalization from public market data.

DexScreener publishes market cap, FDV, liquidity, pair age and per-window price, trade
and maker counts on a pair. It does not publish holder concentration, LP lock state or
token taxes. Those fields stay absent here rather than being guessed from a price: the
API renders an absent field as ``null`` with a reason.

A keyed source may enrich the free payload later. Enrichment is a separate protocol so
the keyless path never constructs a client for a provider the workspace has not keyed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from .dexscreener import DexScreenerClient
from .errors import NotFound
from .registry import get_chain

WINDOWS: tuple[str, ...] = ("5m", "1h", "6h", "24h")
_WINDOW_KEYS = {"5m": "m5", "1h": "h1", "6h": "h6", "24h": "h24"}

SOURCE_DEXSCREENER = "dexscreener"
REASON_QUOTE_SIDE = "QUOTE_SIDE_UNSUPPORTED"
REASON_NO_PAIR = "NO_PRICED_PAIR"


@dataclass(frozen=True)
class WindowCounts:
    """One window of raw provider counters; no value is coerced to zero."""

    price_change_pct: Decimal | None = None
    buys: int | None = None
    sells: int | None = None
    buy_volume_usd: Decimal | None = None
    sell_volume_usd: Decimal | None = None
    buying_makers: int | None = None
    selling_makers: int | None = None


@dataclass(frozen=True)
class MetadataSnapshot:
    """One observed token, before the API derives ages or applies freshness policy.

    ``source`` and ``observed_at`` are the provider's, carried from the exact answer so
    a cached read cannot re-stamp old numbers as current.
    """

    chain: str
    address: str
    source: str
    observed_at: datetime | None
    price_usd: Decimal | None = None
    market_cap_usd: Decimal | None = None
    fdv_usd: Decimal | None = None
    liquidity_usd: Decimal | None = None
    pair_created_at: datetime | None = None
    pair_address: str | None = None
    is_base: bool = True
    windows: dict[str, WindowCounts] = field(default_factory=dict)


class MetadataProvider(Protocol):
    async def token_metadata(self, chain: str, address: str) -> MetadataSnapshot: ...


def _dec(value: object) -> Decimal | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not parsed.is_finite() or parsed != parsed.to_integral_value():
        return None
    return int(parsed)


def _ms(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _map(value: object, key: str) -> dict[str, Any]:
    item = value.get(key) if isinstance(value, dict) else None
    return item if isinstance(item, dict) else {}


def _address_matches(pair: dict[str, Any], address: str, *, evm: bool) -> bool:
    base = _map(pair, "baseToken").get("address")
    quote = _map(pair, "quoteToken").get("address")
    left = str(base or "")
    right = str(quote or "")
    if evm:
        return left.lower() == address.lower() or right.lower() == address.lower()
    return left == address or right == address


def _pair_rank(pair: dict[str, Any]) -> Decimal:
    liquidity = _dec(_map(pair, "liquidity").get("usd"))
    # Unknown liquidity sorts last but does not disqualify the pair: showing a raw price
    # with unknown depth is still more honest than showing no observation at all.
    return liquidity if liquidity is not None else Decimal(-1)


def normalize_metadata_pairs(
    chain: str,
    address: str,
    pairs: list[dict[str, Any]],
    observed_at: datetime | None,
) -> MetadataSnapshot:
    """Pick the deepest pair for ``address`` and read only fields that belong to it.

    Market cap and FDV describe the base token. When the requested token is the quote side
    of the pair, those numbers belong to another token and are withheld, as are the
    pair's own activity counters, which count trades of the base token.
    """
    meta = get_chain(chain)
    evm = meta is not None and meta.family == "evm"
    candidates = [pair for pair in pairs if _address_matches(pair, address, evm=evm)]
    if not candidates:
        raise NotFound(f"no pair for {chain}:{address}")
    pair = max(candidates, key=_pair_rank)
    is_base = str(_map(pair, "baseToken").get("address") or "").lower() == address.lower()

    changes = _map(pair, "priceChange")
    txns = _map(pair, "txns")
    makers = _map(pair, "makers")
    windows: dict[str, WindowCounts] = {}
    for window in WINDOWS:
        key = _WINDOW_KEYS[window]
        if not is_base:
            windows[window] = WindowCounts()
            continue
        bucket = _map(txns, key)
        maker_bucket = _map(makers, key)
        windows[window] = WindowCounts(
            price_change_pct=_dec(changes.get(key)),
            buys=_int(bucket.get("buys")),
            sells=_int(bucket.get("sells")),
            buying_makers=_int(maker_bucket.get("buys")),
            selling_makers=_int(maker_bucket.get("sells")),
        )

    return MetadataSnapshot(
        chain=chain,
        address=address,
        source=SOURCE_DEXSCREENER,
        observed_at=observed_at,
        price_usd=_dec(pair.get("priceUsd")),
        market_cap_usd=_dec(pair.get("marketCap")) if is_base else None,
        fdv_usd=_dec(pair.get("fdv")) if is_base else None,
        liquidity_usd=_dec(_map(pair, "liquidity").get("usd")),
        pair_created_at=_ms(pair.get("pairCreatedAt")),
        pair_address=str(pair.get("pairAddress")) if pair.get("pairAddress") else None,
        is_base=is_base,
        windows=windows,
    )


class DexScreenerMetadataProvider:
    """The free source. One token lookup, reusing the adapter's cache and pacing."""

    def __init__(self, client: DexScreenerClient) -> None:
        self._client = client

    async def token_metadata(self, chain: str, address: str) -> MetadataSnapshot:
        pairs, answered_at = await self._client.token_pairs_observed(chain, address)
        return normalize_metadata_pairs(chain, address, pairs, answered_at)
