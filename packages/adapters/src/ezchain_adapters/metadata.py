"""Token metadata sources: free first, keyed providers only with a user key.

The endpoint this feeds is a read model: it combines DexScreener's public pair payload
with an optional public-RPC authority check and, only when the caller supplies a credential,
Birdeye enrichment. Nothing here runs in a browser; every request leaves the process
through an HTTP client owned by the API. Normalization keeps ``None`` for a field the
source did not report so the read model can answer ``unknown`` instead of a guessed zero.

The SPL mint layout used by the authority check is fixed: 4-byte mint-authority option at
offset 0 and 4-byte freeze-authority option at offset 46, where zero means revoked. A
payload that is too short or malformed yields no verdict, never a false "revoked".
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Self, cast

import httpx

from ezchain_core import SourceRef

from .dexscreener import normalize_pair
from .errors import AdapterError
from .http import JsonApiClient

WINDOWS: tuple[str, ...] = ("5m", "1h", "6h", "24h")
WINDOW_SECONDS: dict[str, int] = {
    "5m": 300,
    "1h": 3600,
    "6h": 21600,
    "24h": 86400,
}
DEX_SCREENER_WINDOW_KEYS: dict[str, str] = {
    "m5": "5m",
    "h1": "1h",
    "h6": "6h",
    "h24": "24h",
}
SPL_MINT_ACCOUNT_BYTES = 82
SPL_FREEZE_OPTION_OFFSET = 46
SPL_CHAINS = frozenset({"solana"})


@dataclass(frozen=True)
class WindowFacts:
    """One window's activity counts. Every field is optional and stays null when unknown."""

    price_change_pct: Decimal | None = None
    buys: int | None = None
    sells: int | None = None
    buy_volume_usd: Decimal | None = None
    sell_volume_usd: Decimal | None = None
    buying_makers: int | None = None
    selling_makers: int | None = None


@dataclass(frozen=True)
class TokenMetadataObservation:
    """What the sources actually answered for one token, with per-group provenance.

    ``source`` is the primary free source. ``sources`` overrides it per group, so a
    market-cap number can name Birdeye while the price still names DexScreener. A group
    with no override inherits the primary source; the read model attaches that source to
    every metric, known or unknown, because "asked and not answered" differs from "not
    asked".
    """

    chain: str
    address: str
    source: SourceRef | None = None
    sources: Mapping[str, SourceRef] = field(default_factory=dict)
    symbol: str | None = None
    name: str | None = None
    price_usd: Decimal | None = None
    market_cap_usd: Decimal | None = None
    fdv_usd: Decimal | None = None
    liquidity_usd: Decimal | None = None
    liquidity_change_pct: Decimal | None = None
    lp_burned_pct: Decimal | None = None
    lp_locked_pct: Decimal | None = None
    lp_lock_duration_seconds: int | None = None
    holders: int | None = None
    holders_change_h1_pct: Decimal | None = None
    top_10_pct: Decimal | None = None
    dev_holdings_pct: Decimal | None = None
    pair_created_at: datetime | None = None
    windows: Mapping[str, WindowFacts] = field(default_factory=dict)
    mint_revoked: bool | None = None
    freeze_revoked: bool | None = None
    sells_work: bool | None = None
    buy_tax_pct: Decimal | None = None
    sell_tax_pct: Decimal | None = None
    degraded: tuple[str, ...] = ()

    def source_for(self, group: str) -> SourceRef | None:
        return self.sources.get(group, self.source)


def _dec(value: object) -> Decimal | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _int(value: object) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return max(0, parsed)


def _bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "1", "yes"}:
        return True
    if isinstance(value, str) and value.strip().lower() in {"false", "0", "no"}:
        return False
    return None


def _text(value: object) -> str | None:
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


def _pair_window_facts(pair: Mapping[str, Any]) -> dict[str, WindowFacts]:
    txns = pair.get("txns")
    txns = txns if isinstance(txns, Mapping) else {}
    changes = pair.get("priceChange")
    changes = changes if isinstance(changes, Mapping) else {}
    windows: dict[str, WindowFacts] = {}
    for source_key, window in DEX_SCREENER_WINDOW_KEYS.items():
        counts = txns.get(source_key)
        counts = counts if isinstance(counts, Mapping) else {}
        windows[window] = WindowFacts(
            price_change_pct=_dec(changes.get(source_key)),
            buys=_int(counts.get("buys")),
            sells=_int(counts.get("sells")),
        )
    return windows


def normalize_dexscreener_pairs(
    chain: str,
    address: str,
    pairs: Mapping[str, Any] | list[Any],
    *,
    observed_at: datetime | None = None,
    source: SourceRef | None = None,
) -> TokenMetadataObservation:
    """Fold one token's public pair payload into a provenanced observation.

    The requested token may be the quote side of a pair; its USD price is then derived
    from ``priceNative``. A missing or non-positive native price produces no price rather
    than the base token's. Price changes for a quote-side token are not reported by the
    provider and stay unknown.
    """
    observed = observed_at or datetime.now(UTC)
    ref = source or SourceRef("dexscreener", "https://dexscreener.com", observed)
    base = TokenMetadataObservation(
        chain=chain,
        address=address,
        source=ref,
        sources={"market": ref, "windows": ref},
    )
    if not isinstance(pairs, (list, tuple)):
        return replace(base, degraded=("NO_PAIRS:dexscreener",))

    candidates: list[tuple[bool, TokenMetadataObservation]] = []
    for raw in pairs:
        row = normalize_pair(raw, observed)
        if row is None or row.chain != chain:
            continue
        is_base = row.base_token == address
        if not is_base and row.quote_token != address:
            continue
        price = row.price_usd if is_base else None
        if not is_base:
            native = row.price_native
            if native is not None and native > 0:
                price = row.price_usd / native
        if price is None or price <= 0:
            continue
        pair = raw if isinstance(raw, Mapping) else {}
        symbol = row.symbol if is_base else row.quote_symbol
        name = row.name if is_base else row.quote_name
        changes = (
            (
                row.price_change_m5,
                row.price_change_h1,
                row.price_change_h6,
                row.price_change_h24,
            )
            if is_base
            else (None, None, None, None)
        )
        windows = {
            window: replace(facts, price_change_pct=change)
            for window, facts, change in zip(
                WINDOWS,
                (_pair_window_facts(pair).get(window, WindowFacts()) for window in WINDOWS),
                changes,
                strict=True,
            )
        }
        liquidity = row.liquidity_usd
        candidate = replace(
            base,
            symbol=symbol,
            name=name,
            price_usd=price,
            liquidity_usd=liquidity,
            pair_created_at=row.pair_created_at,
            windows=windows,
        )
        candidates.append((is_base, candidate))

    if not candidates:
        return replace(base, degraded=("NO_PAIRS:dexscreener",))
    # Several pairs can quote the same token. Prefer the token's base side and, within
    # that side, the deepest pair, so one shallow listing cannot set the displayed price.
    base_candidates = [item for is_base, item in candidates if is_base]
    pool = base_candidates or [item for _, item in candidates]

    def depth(item: TokenMetadataObservation) -> tuple[bool, Decimal]:
        liquidity = item.liquidity_usd
        return (liquidity is not None, liquidity or Decimal(0))

    return max(pool, key=depth)


def parse_mint_authorities(data: object) -> tuple[bool | None, bool | None]:
    """Decode the two authority options of a base64 SPL mint account.

    A payload that is not a valid full mint account yields two unknowns. A zero option
    means the corresponding authority is revoked; a non-zero option means it is present.
    """
    if not isinstance(data, str):
        return None, None
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return None, None
    if len(raw) < SPL_MINT_ACCOUNT_BYTES:
        return None, None
    mint_revoked = int.from_bytes(raw[0:4], "little") == 0
    freeze_revoked = (
        int.from_bytes(raw[SPL_FREEZE_OPTION_OFFSET : SPL_FREEZE_OPTION_OFFSET + 4], "little") == 0
    )
    return mint_revoked, freeze_revoked


_BIRDEYE_ALIASES: dict[str, tuple[str, ...]] = {
    "market_cap_usd": ("market_cap_usd", "marketcap", "marketCap"),
    "fdv_usd": ("fdv_usd", "fdv"),
    "liquidity_change_pct": ("liquidity_change_pct", "liquidityChangePercent"),
    "lp_burned_pct": ("lp_burned_pct", "lpBurnedPercent"),
    "lp_locked_pct": ("lp_locked_pct", "lpLockedPercent"),
    "lp_lock_duration_seconds": ("lp_lock_duration_seconds", "lpLockDurationSeconds"),
    "holders": ("holders", "holder"),
    "holders_change_h1_pct": ("holders_change_h1_pct", "holderChange1hPercent"),
    "top_10_pct": ("top_10_pct", "top10HolderPercent"),
    "dev_holdings_pct": ("dev_holdings_pct", "devHoldingsPercent"),
    "sells_work": ("sells_work", "sellsWork"),
    "buy_tax_pct": ("buy_tax_pct", "buyTaxPercent"),
    "sell_tax_pct": ("sell_tax_pct", "sellTaxPercent"),
}


def normalize_birdeye(
    payload: object,
    *,
    source: SourceRef,
    chain: str,
    address: str,
) -> TokenMetadataObservation:
    """Map a recorded Birdeye enrichment payload onto an observation.

    Only fields the payload supplies are set. The returned observation is a partial
    overlay: callers merge it into the free-source observation so an absent key never
    erases a value the free source did report.
    """
    data = payload.get("data", payload) if isinstance(payload, Mapping) else {}
    data = data if isinstance(data, Mapping) else {}

    def pick(field_name: str) -> object:
        for key in _BIRDEYE_ALIASES[field_name]:
            if key in data:
                return data[key]
        return None

    values: dict[str, Any] = {}
    for name in (
        "market_cap_usd",
        "fdv_usd",
        "liquidity_change_pct",
        "lp_burned_pct",
        "lp_locked_pct",
        "top_10_pct",
        "holders_change_h1_pct",
        "buy_tax_pct",
        "sell_tax_pct",
    ):
        values[name] = _dec(pick(name))
    for name in ("holders", "lp_lock_duration_seconds"):
        values[name] = _int(pick(name))
    values["sells_work"] = _bool(pick("sells_work"))

    windows: dict[str, WindowFacts] = {}
    raw_windows = data.get("windows")
    raw_windows = raw_windows if isinstance(raw_windows, Mapping) else {}
    for window in WINDOWS:
        item = raw_windows.get(window)
        item = item if isinstance(item, Mapping) else {}
        windows[window] = WindowFacts(
            buy_volume_usd=_dec(item.get("buy_volume_usd")),
            sell_volume_usd=_dec(item.get("sell_volume_usd")),
            buying_makers=_int(item.get("buying_makers")),
            selling_makers=_int(item.get("selling_makers")),
        )

    # Only claim a group's provenance when this payload actually supplied a field in it.
    # A window with only a maker count must not relabel the free buys count as Birdeye's.
    sources: dict[str, SourceRef] = {}
    if any(
        values.get(name) is not None
        for name in ("market_cap_usd", "fdv_usd", "liquidity_change_pct")
    ):
        sources["market"] = source
    if any(
        values.get(name) is not None
        for name in ("lp_burned_pct", "lp_locked_pct", "lp_lock_duration_seconds")
    ):
        sources["liquidity"] = source
    if any(
        values.get(name) is not None
        for name in ("holders", "holders_change_h1_pct", "top_10_pct", "dev_holdings_pct")
    ):
        sources["holders"] = source
    for window, fact in windows.items():
        if (
            fact.buy_volume_usd is not None
            or fact.sell_volume_usd is not None
            or fact.buying_makers is not None
            or fact.selling_makers is not None
        ):
            sources[f"windows:{window}"] = source
    if any(values.get(name) is not None for name in ("sells_work", "buy_tax_pct", "sell_tax_pct")):
        sources["safety"] = source

    return TokenMetadataObservation(
        chain=chain,
        address=address,
        source=source,
        sources=sources,
        windows=windows,
        **values,
    )


def merge_observations(
    primary: TokenMetadataObservation, *overlays: TokenMetadataObservation
) -> TokenMetadataObservation:
    """Merge keyed overlays into the free observation without inventing values.

    A field set by an overlay replaces the primary; an unset overlay field leaves the
    primary value and its provenance untouched. Window facts merge field by field for the
    same reason: a keyed maker count must not drop a free buys count.
    """
    values = primary.__dict__.copy()
    sources = dict(primary.sources)
    windows = dict(primary.windows)
    degraded = list(primary.degraded)
    for overlay in overlays:
        for name, value in overlay.__dict__.items():
            if name in {"chain", "address", "source", "sources", "windows", "degraded"}:
                continue
            if value is not None and not (isinstance(value, Mapping) and not value):
                values[name] = value
        sources.update(overlay.sources)
        for window, facts in overlay.windows.items():
            current = windows.get(window, WindowFacts())
            windows[window] = replace(
                current,
                **{name: value for name, value in facts.__dict__.items() if value is not None},
            )
        degraded.extend(overlay.degraded)
    values["sources"] = sources
    values["windows"] = windows
    values["degraded"] = tuple(degraded)
    return TokenMetadataObservation(**values)


class PublicRpcClient:
    """A keyless JSON-RPC reader for SPL mint authorities.

    The endpoint is injected so a self-hosted operator can point at their own public RPC;
    tests supply a throwaway local transport. No credential is sent and no address is
    hard-coded.
    """

    def __init__(self, endpoint: str, *, timeout: float = 10.0) -> None:
        self.endpoint = endpoint
        self._timeout = timeout

    async def mint_authorities(self, address: str) -> tuple[bool | None, bool | None]:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [address, {"encoding": "base64"}],
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self.endpoint, json=payload)
        response.raise_for_status()
        body = response.json()
        result = body.get("result") if isinstance(body, Mapping) else None
        if not isinstance(result, Mapping):
            return None, None
        value = result.get("value")
        if not isinstance(value, Mapping):
            return None, None
        data = value.get("data")
        if isinstance(data, list):
            encoded = data[0] if data else None
        elif isinstance(data, str):
            encoded = data
        else:
            encoded = None
        return parse_mint_authorities(encoded)


class BirdeyeClient(JsonApiClient):
    """Birdeye enrichment. A key is required: without one the client is never built."""

    label = "Birdeye"
    base_url = "https://public-api.birdeye.so"
    keyed_base_url = "https://public-api.birdeye.so"
    cache_ttl_seconds = 30.0
    rate_per_second = 2.0
    burst = 2

    def __init__(self, chain: str, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._chain = chain

    async def __aenter__(self) -> Self:
        # Chain scoping is mandatory on Birdeye; the header lives on the HTTP client so a
        # call site cannot forget it and the base client still owns the credential header.
        await super().__aenter__()
        if self._client is not None:
            self._client.headers.update({"X-Chain": self._chain})
        return self

    async def enrichment(self, address: str) -> object:
        async with self:
            return await self._get(
                "/defi/v3/token/metadata",
                {"address": address},
            )


KeyProvider = Callable[[], Awaitable[str | None]]


class TokenMetadataClient:
    """Fetch token metadata from free sources, then keyed sources only when supplied.

    Each source is isolated: one failure adds a degraded reason and keeps the values the
    other sources answered. A total outage therefore returns an empty, provenance-free
    observation instead of raising into the read model.
    """

    def __init__(
        self,
        *,
        dexscreener: Any = None,
        rpc: Any = None,
        birdeye: Any = None,
        key_provider: KeyProvider | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        from .dexscreener import DexScreenerClient

        self._dexscreener = dexscreener or DexScreenerClient()
        self._rpc = rpc
        self._birdeye = birdeye
        self._key_provider = key_provider
        self._now = now or (lambda: datetime.now(UTC))

    async def _keyed_source(self) -> BirdeyeClient | None:
        """Return the keyed client only when a user key is actually available."""
        if self._birdeye is None or self._key_provider is None:
            return None
        key = await self._key_provider()
        if not key:
            return None
        return cast(BirdeyeClient, self._birdeye)

    async def fetch(self, chain: str, address: str) -> TokenMetadataObservation:
        now = self._now()
        degraded: list[str] = []
        observed = now
        pairs: Any = []
        try:
            pairs, answered = await self._dexscreener.token_pairs_observed(chain, address)
            observed = answered or now
            free = normalize_dexscreener_pairs(chain, address, pairs, observed_at=observed)
            degraded.extend(free.degraded)
        except AdapterError as exc:
            free = TokenMetadataObservation(
                chain=chain, address=address, degraded=(f"UNAVAILABLE:dexscreener:{exc.code}",)
            )
            degraded.append(free.degraded[0])
        except Exception:
            free = TokenMetadataObservation(
                chain=chain, address=address, degraded=("UNAVAILABLE:dexscreener",)
            )
            degraded.append(free.degraded[0])

        overlays: list[TokenMetadataObservation] = []
        if self._rpc is not None and chain in SPL_CHAINS:
            try:
                mint_revoked, freeze_revoked = await self._rpc.mint_authorities(address)
                if mint_revoked is not None or freeze_revoked is not None:
                    rpc_source = SourceRef(
                        "solana-rpc", getattr(self._rpc, "endpoint", None), observed
                    )
                    overlays.append(
                        TokenMetadataObservation(
                            chain=chain,
                            address=address,
                            source=rpc_source,
                            sources={"rpc": rpc_source},
                            mint_revoked=mint_revoked,
                            freeze_revoked=freeze_revoked,
                        )
                    )
            except (AdapterError, httpx.HTTPError, ValueError):
                degraded.append("UNAVAILABLE:public-rpc")

        keyed = await self._keyed_source()
        if keyed is not None:
            try:
                payload = await keyed.enrichment(address)
                birdeye_source = SourceRef("birdeye", BirdeyeClient.base_url, observed)
                overlays.append(
                    normalize_birdeye(payload, source=birdeye_source, chain=chain, address=address)
                )
            except AdapterError as exc:
                degraded.append(f"UNAVAILABLE:birdeye:{exc.code}")
            except (httpx.HTTPError, ValueError):
                degraded.append("UNAVAILABLE:birdeye")

        merged = merge_observations(free, *overlays)
        return replace(merged, degraded=tuple(dict.fromkeys(degraded)))


__all__ = [
    "WINDOWS",
    "WINDOW_SECONDS",
    "BirdeyeClient",
    "PublicRpcClient",
    "TokenMetadataClient",
    "TokenMetadataObservation",
    "WindowFacts",
    "merge_observations",
    "normalize_birdeye",
    "normalize_dexscreener_pairs",
    "parse_mint_authorities",
]
