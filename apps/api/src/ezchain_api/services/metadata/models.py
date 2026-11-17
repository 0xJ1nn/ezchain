"""Domain values for one token's metadata read.

Every displayed number is an :class:`Observation`: value, unit, source, observation time
and, when nothing was measured, the reason. A missing value is never zero and a check is
never a pass on absent evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

# Stable reasons. A reader compares on these, never on a human message.
REASON_SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
REASON_ENRICHER_UNAVAILABLE = "ENRICHER_UNAVAILABLE"
REASON_FIELD_UNAVAILABLE = "FIELD_UNAVAILABLE"
REASON_NO_KEYED_SOURCE = "KEYED_SOURCE_NOT_CONFIGURED"
REASON_QUOTE_SIDE = "QUOTE_SIDE_UNSUPPORTED"
REASON_NO_PAIR = "NO_PRICED_PAIR"
REASON_WINDOW_OUTSIDE_LIFE = "WINDOW_OUTSIDE_PAIR_LIFE"
REASON_LIQUIDITY_CHANGE = "LIQUIDITY_CHANGE_UNAVAILABLE"
REASON_SPLIT_VOLUME = "BUY_SELL_VOLUME_UNAVAILABLE"
REASON_PAIR_AGE_UNKNOWN = "PAIR_AGE_UNKNOWN"
REASON_PAIR_AGE_FUTURE = "PAIR_AGE_FUTURE"
REASON_SAFETY = "SAFETY_NOT_IMPLEMENTED"
REASON_BSR = "BSR_UNAVAILABLE"

# A combined buy and sell tax above 10 % fails the check; the bound is a display verdict,
# not a trade gate, and is stated in the API documentation beside the check.
MAX_TAX_BPS = Decimal(1000)
MAX_TOP_10_PCT = Decimal(30)
MAX_DEV_PCT = Decimal(5)


@dataclass(frozen=True)
class Observation:
    value: Decimal | int | bool | str | datetime | None = None
    unit: str = "usd"
    source: str | None = None
    observed_at: datetime | None = None
    reason: str | None = None

    @property
    def known(self) -> bool:
        return self.value is not None

    @classmethod
    def missing(
        cls,
        reason: str,
        *,
        unit: str,
        source: str | None = None,
        observed_at: datetime | None = None,
    ) -> Observation:
        return cls(unit=unit, source=source, observed_at=observed_at, reason=reason)


@dataclass(frozen=True)
class WindowMetrics:
    applicable: bool
    price_change_pct: Observation
    buys: Observation
    sells: Observation
    buy_volume_usd: Observation
    sell_volume_usd: Observation
    buying_makers: Observation
    selling_makers: Observation
    bsr: Observation


@dataclass(frozen=True)
class LPState:
    status: str  # burned | locked | unlocked | unknown
    burned_pct: Observation
    locked_pct: Observation
    lock_duration_seconds: Observation
    source: str | None
    observed_at: datetime | None


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # pass | fail | unknown
    observation: Observation
    reason: str | None = None


@dataclass(frozen=True)
class Enrichment:
    """Fields a keyed provider can supply. All are absent until one is configured."""

    holders: int | None = None
    holders_change_h1_pct: Decimal | None = None
    top_10_pct: Decimal | None = None
    dev_holdings_pct: Decimal | None = None
    lp_status: str = "unknown"
    lp_burned_pct: Decimal | None = None
    lp_locked_pct: Decimal | None = None
    lp_lock_duration_seconds: int | None = None
    mint_revoked: bool | None = None
    freeze_revoked: bool | None = None
    sells_work: bool | None = None
    buy_tax_bps: Decimal | None = None
    sell_tax_bps: Decimal | None = None
    source: str | None = None
    observed_at: datetime | None = None


@dataclass(frozen=True)
class TokenMetadata:
    chain: str
    address: str
    as_of: datetime
    market_cap_usd: Observation
    fdv_usd: Observation
    liquidity_usd: Observation
    liquidity_change_pct: Observation
    lp: LPState
    holders: Observation
    holders_change_h1_pct: Observation
    top_10_pct: Observation
    dev_holdings_pct: Observation
    pair_age_seconds: Observation
    windows: dict[str, WindowMetrics]
    checks: dict[str, Check]
    warnings: tuple[str, ...]
