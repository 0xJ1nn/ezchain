"""Compose one token's metadata from observed fields.

The provider answer is cached per token with a short TTL; ages and derived verdicts are
computed on every read so a cached answer cannot freeze a pair's age. A source failure
returns the fields that are still known plus a warning, never a filled-in zero.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from ezchain_adapters import AdapterError, get_chain
from ezchain_adapters.cache import MISSING, BoundedTTLCache
from ezchain_adapters.metadata import WINDOWS, MetadataProvider, MetadataSnapshot, WindowCounts
from ezchain_core import utcnow

from .models import (
    MAX_DEV_PCT,
    MAX_TAX_BPS,
    MAX_TOP_10_PCT,
    REASON_BSR,
    REASON_FIELD_UNAVAILABLE,
    REASON_LIQUIDITY_CHANGE,
    REASON_NO_KEYED_SOURCE,
    REASON_PAIR_AGE_FUTURE,
    REASON_PAIR_AGE_UNKNOWN,
    REASON_QUOTE_SIDE,
    REASON_SPLIT_VOLUME,
    REASON_WINDOW_OUTSIDE_LIFE,
    Check,
    Enrichment,
    LPState,
    Observation,
    TokenMetadata,
    WindowMetrics,
)

CACHE_TTL_SECONDS = 15.0
CACHE_MAX_ENTRIES = 256
CLOCK_SKEW_SECONDS = 60
_WINDOW_SECONDS = {"5m": 300, "1h": 3600, "6h": 21600, "24h": 86400}
_LP_STATUSES = frozenset({"burned", "locked", "unlocked"})


class Enricher(Protocol):
    async def enrich(self, chain: str, address: str) -> Enrichment: ...


class NoEnricher:
    """The keyless default: no holders, LP, tax or authority evidence exists."""

    async def enrich(self, chain: str, address: str) -> Enrichment:
        return Enrichment()


def _cache_key(chain: str, address: str) -> str:
    meta = get_chain(chain)
    value = address.lower() if meta is not None and meta.family == "evm" else address
    return f"{chain.lower()}:{value}"


def _verdict(value: Decimal | None, ceiling: Decimal) -> str:
    if value is None:
        return "unknown"
    return "pass" if value <= ceiling else "fail"


class MetadataService:
    def __init__(
        self,
        provider: MetadataProvider,
        enricher: Enricher | None = None,
        *,
        cache: BoundedTTLCache | None = None,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._provider = provider
        self._enricher = enricher if enricher is not None else NoEnricher()
        self._cache = cache or BoundedTTLCache(
            max_entries=CACHE_MAX_ENTRIES, ttl_seconds=CACHE_TTL_SECONDS
        )
        self._now = now

    async def get(self, chain: str, address: str) -> TokenMetadata:
        key = _cache_key(chain, address)
        cached = self._cache.get(key)
        warnings: list[str] = []
        if cached is MISSING:
            snapshot, enrichment = await self._load(chain, address, warnings)
            self._cache.set(key, (snapshot, enrichment))
        else:
            snapshot, enrichment = cached
        return self._build(chain, address, snapshot, enrichment, tuple(warnings), self._now())

    async def _load(
        self, chain: str, address: str, warnings: list[str]
    ) -> tuple[object, Enrichment]:
        try:
            snapshot: object = await self._provider.token_metadata(chain, address)
        except AdapterError:
            snapshot = MetadataSnapshot(
                chain=chain,
                address=address,
                source="dexscreener",
                observed_at=None,
            )
            warnings.append("SOURCE_UNAVAILABLE")

        try:
            enrichment = await self._enricher.enrich(chain, address)
        except AdapterError:
            enrichment = Enrichment()
            warnings.append("ENRICHER_UNAVAILABLE")
        return snapshot, enrichment

    def _build(
        self,
        chain: str,
        address: str,
        snapshot: object,
        enrichment: Enrichment,
        warnings: tuple[str, ...],
        now: datetime,
    ) -> TokenMetadata:
        assert isinstance(snapshot, MetadataSnapshot)
        src: str | None = snapshot.source
        observed: datetime | None = snapshot.observed_at
        no_data = not warnings or "SOURCE_UNAVAILABLE" not in warnings
        reason = None if no_data else "SOURCE_UNAVAILABLE"
        if not no_data:
            src = None
            observed = None

        def free(
            value: Decimal | None, unit: str = "usd", *, quote_only: bool = False
        ) -> Observation:
            if value is not None:
                return Observation(value=value, unit=unit, source=src, observed_at=observed)
            return Observation.missing(
                REASON_QUOTE_SIDE
                if quote_only and snapshot.is_base is False
                else reason or REASON_FIELD_UNAVAILABLE,
                unit=unit,
                source=src,
                observed_at=observed,
            )

        market_cap = free(snapshot.market_cap_usd, quote_only=True)
        fdv = free(snapshot.fdv_usd, quote_only=True)

        if snapshot.pair_created_at is None:
            pair_age = Observation.missing(
                reason or REASON_PAIR_AGE_UNKNOWN, unit="seconds", source=src, observed_at=observed
            )
        elif snapshot.pair_created_at > now + timedelta(seconds=CLOCK_SKEW_SECONDS):
            pair_age = Observation.missing(
                REASON_PAIR_AGE_FUTURE, unit="seconds", source=src, observed_at=observed
            )
        else:
            pair_age = Observation(
                value=max(0, int((now - snapshot.pair_created_at).total_seconds())),
                unit="seconds",
                source=src,
                observed_at=observed,
            )

        keyed = enrichment.source
        keyed_at = enrichment.observed_at
        absent = REASON_NO_KEYED_SOURCE if keyed is None else REASON_FIELD_UNAVAILABLE

        def enriched(value: Decimal | int | bool | None, unit: str) -> Observation:
            if value is not None:
                return Observation(value=value, unit=unit, source=keyed, observed_at=keyed_at)
            return Observation.missing(absent, unit=unit, source=keyed, observed_at=keyed_at)

        lp_status = enrichment.lp_status if enrichment.lp_status in _LP_STATUSES else "unknown"
        lp = LPState(
            status=lp_status,
            burned_pct=enriched(enrichment.lp_burned_pct, "percent"),
            locked_pct=enriched(enrichment.lp_locked_pct, "percent"),
            lock_duration_seconds=enriched(enrichment.lp_lock_duration_seconds, "seconds"),
            source=keyed,
            observed_at=keyed_at,
        )

        windows: dict[str, WindowMetrics] = {}
        for window in WINDOWS:
            seconds = _WINDOW_SECONDS[window]
            applies = (
                pair_age.known and isinstance(pair_age.value, int) and pair_age.value >= seconds
            )
            raw = snapshot.windows.get(window)
            if not applies:
                window_reason = (
                    reason
                    if reason
                    else REASON_PAIR_AGE_UNKNOWN
                    if not pair_age.known
                    else REASON_WINDOW_OUTSIDE_LIFE
                )
                blank = Observation.missing(
                    window_reason, unit="count", source=src, observed_at=observed
                )
                windows[window] = WindowMetrics(
                    applicable=False,
                    price_change_pct=Observation.missing(
                        window_reason,
                        unit="percent",
                        source=src,
                        observed_at=observed,
                    ),
                    buys=blank,
                    sells=blank,
                    buy_volume_usd=Observation.missing(
                        window_reason,
                        unit="usd",
                        source=src,
                        observed_at=observed,
                    ),
                    sell_volume_usd=Observation.missing(
                        window_reason,
                        unit="usd",
                        source=src,
                        observed_at=observed,
                    ),
                    buying_makers=blank,
                    selling_makers=blank,
                    bsr=Observation.missing(
                        window_reason,
                        unit="ratio",
                        source=src,
                        observed_at=observed,
                    ),
                )
                continue

            counts = raw if raw is not None else WindowCounts()
            buys = (
                Observation(value=counts.buys, unit="count", source=src, observed_at=observed)
                if counts.buys is not None
                else Observation.missing(
                    REASON_FIELD_UNAVAILABLE, unit="count", source=src, observed_at=observed
                )
            )
            sells = (
                Observation(value=counts.sells, unit="count", source=src, observed_at=observed)
                if counts.sells is not None
                else Observation.missing(
                    REASON_FIELD_UNAVAILABLE, unit="count", source=src, observed_at=observed
                )
            )
            if isinstance(buys.value, int) and isinstance(sells.value, int) and sells.value > 0:
                bsr: Observation = Observation(
                    value=Decimal(buys.value) / Decimal(sells.value),
                    unit="ratio",
                    source=src,
                    observed_at=observed,
                )
            else:
                bsr = Observation.missing(
                    REASON_BSR, unit="ratio", source=src, observed_at=observed
                )
            windows[window] = WindowMetrics(
                applicable=True,
                price_change_pct=Observation(
                    value=counts.price_change_pct,
                    unit="percent",
                    source=src,
                    observed_at=observed,
                )
                if counts.price_change_pct is not None
                else Observation.missing(
                    REASON_FIELD_UNAVAILABLE,
                    unit="percent",
                    source=src,
                    observed_at=observed,
                ),
                buys=buys,
                sells=sells,
                buy_volume_usd=Observation.missing(
                    REASON_SPLIT_VOLUME, unit="usd", source=src, observed_at=observed
                ),
                sell_volume_usd=Observation.missing(
                    REASON_SPLIT_VOLUME, unit="usd", source=src, observed_at=observed
                ),
                buying_makers=Observation(
                    value=counts.buying_makers,
                    unit="count",
                    source=src,
                    observed_at=observed,
                )
                if counts.buying_makers is not None
                else Observation.missing(
                    REASON_FIELD_UNAVAILABLE, unit="count", source=src, observed_at=observed
                ),
                selling_makers=Observation(
                    value=counts.selling_makers,
                    unit="count",
                    source=src,
                    observed_at=observed,
                )
                if counts.selling_makers is not None
                else Observation.missing(
                    REASON_FIELD_UNAVAILABLE, unit="count", source=src, observed_at=observed
                ),
                bsr=bsr,
            )

        checks = self._checks(enrichment, lp, absent, keyed, keyed_at)
        return TokenMetadata(
            chain=chain,
            address=address,
            as_of=now,
            market_cap_usd=market_cap,
            fdv_usd=fdv,
            liquidity_usd=free(snapshot.liquidity_usd),
            liquidity_change_pct=Observation.missing(
                REASON_LIQUIDITY_CHANGE, unit="percent", source=src, observed_at=observed
            ),
            lp=lp,
            holders=enriched(enrichment.holders, "count"),
            holders_change_h1_pct=enriched(enrichment.holders_change_h1_pct, "percent"),
            top_10_pct=enriched(enrichment.top_10_pct, "percent"),
            dev_holdings_pct=enriched(enrichment.dev_holdings_pct, "percent"),
            pair_age_seconds=pair_age,
            windows=windows,
            checks=checks,
            warnings=warnings,
        )

    def _checks(
        self,
        enrichment: Enrichment,
        lp: LPState,
        absent: str,
        source: str | None,
        observed_at: datetime | None,
    ) -> dict[str, Check]:
        def boolean(name: str, value: bool | None) -> Check:
            observation = (
                Observation(value=value, unit="boolean", source=source, observed_at=observed_at)
                if value is not None
                else Observation.missing(
                    absent, unit="boolean", source=source, observed_at=observed_at
                )
            )
            status = "unknown" if value is None else "pass" if value else "fail"
            return Check(name=name, status=status, observation=observation)

        lp_ok = lp.status in ("burned", "locked")
        lp_observation = (
            Observation(value=lp.status, unit="state", source=source, observed_at=observed_at)
            if lp.status != "unknown"
            else Observation.missing(absent, unit="state", source=source, observed_at=observed_at)
        )
        top = enriched_number(enrichment.top_10_pct, "percent", absent, source, observed_at)
        dev = enriched_number(enrichment.dev_holdings_pct, "percent", absent, source, observed_at)
        taxes = (
            max(enrichment.buy_tax_bps, enrichment.sell_tax_bps)
            if enrichment.buy_tax_bps is not None and enrichment.sell_tax_bps is not None
            else None
        )
        tax_observation = enriched_number(taxes, "bps", absent, source, observed_at)
        return {
            "mint_revoked": boolean("mint_revoked", enrichment.mint_revoked),
            "freeze_revoked": boolean("freeze_revoked", enrichment.freeze_revoked),
            "lp_burned_or_locked": Check(
                name="lp_burned_or_locked",
                status="unknown" if lp.status == "unknown" else "pass" if lp_ok else "fail",
                observation=lp_observation,
            ),
            "top_10_under_30": Check(
                name="top_10_under_30",
                status=_verdict(enrichment.top_10_pct, MAX_TOP_10_PCT),
                observation=top,
            ),
            "dev_under_5": Check(
                name="dev_under_5",
                status=_verdict(enrichment.dev_holdings_pct, MAX_DEV_PCT),
                observation=dev,
            ),
            "sells_work": boolean("sells_work", enrichment.sells_work),
            "buy_and_sell_tax": Check(
                name="buy_and_sell_tax",
                status=_verdict(taxes, MAX_TAX_BPS),
                observation=tax_observation,
            ),
        }


def enriched_number(
    value: Decimal | None,
    unit: str,
    absent: str,
    source: str | None,
    observed_at: datetime | None,
) -> Observation:
    if value is not None:
        return Observation(value=value, unit=unit, source=source, observed_at=observed_at)
    return Observation.missing(absent, unit=unit, source=source, observed_at=observed_at)
