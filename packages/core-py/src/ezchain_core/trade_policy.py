"""The trade policy: the risk fields ``evaluate_order`` consumes, one profile registry.

The order gate is the truth. This module exposes the five ``RiskLimits`` fields it reads
for sizing (minimum, absolute maximum, percent of equity, per-token cap and slippage) with
their range and description, and bundles them into the three named profiles. Sizing is
checked with :func:`ezchain_core.risk.size_reasons`, the same helper the order gate calls,
so a preview cannot disagree with the order it previews.

No number is invented here: a profile's values are read from ``TRADE_RISK_PROFILES``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from .risk import TRADE_RISK_PROFILES, RiskLimits, TradeRiskProfile, size_reasons
from .types import RiskReason

ProfileName = Literal["low", "mid", "high"]
PolicyKind = Literal["decimal", "integer", "ratio"]

PROFILE_ORDER: tuple[ProfileName, ...] = ("low", "mid", "high")

# How loose a declared profile is: ``low`` 1, ``mid`` 2, ``high`` 3, no profile 0. The
# registry is ordered from tightest to loosest, so a higher rank admits more risk.
PROFILE_RANK: dict[str, int] = {"low": 1, "mid": 2, "high": 3}

PROFILE_LABELS: dict[str, str] = {
    "low": "Low risk",
    "mid": "Mid risk",
    "high": "High risk",
}

PROFILE_DESCRIPTIONS: dict[str, str] = {
    "low": (
        "Smaller sizes, a tight slippage ceiling and a low per-token cap. Choosing it can "
        "only tighten the deployment default."
    ),
    "mid": (
        "The balanced profile: a $5 minimum, a $100 absolute maximum and at most 5% of "
        "equity in one buy."
    ),
    "high": (
        "Larger sizes and a wider slippage ceiling for a deliberately aggressive account. "
        "Choosing it loosens a fund-affecting limit and needs acknowledgement."
    ),
}


@dataclass(frozen=True)
class PolicyField:
    """One sizing field the order gate reads, with the range a value may take."""

    key: str
    label: str
    unit: str
    kind: PolicyKind
    description: str
    minimum: str
    maximum: str


POLICY_FIELDS: tuple[PolicyField, ...] = (
    PolicyField(
        key="min_order_usd",
        label="Minimum order size",
        unit="USD",
        kind="decimal",
        description=(
            "A buy below this size is refused with SIZE_BELOW_MIN. The percentage floor is "
            "a separate bound; both apply."
        ),
        minimum="0",
        maximum="1000000",
    ),
    PolicyField(
        key="max_order_usd",
        label="Maximum order size",
        unit="USD",
        kind="decimal",
        description=(
            "The absolute ceiling for one buy. A size above it is refused with SIZE_ABOVE_MAX."
        ),
        minimum="1",
        maximum="1000000",
    ),
    PolicyField(
        key="max_order_pct_of_equity",
        label="Maximum share of equity",
        unit="ratio",
        kind="ratio",
        description=(
            "A buy may use at most this share of account equity, as a ratio (0.05 = 5%). "
            "When equity is unknown the buy is refused with EQUITY_UNKNOWN."
        ),
        minimum="0",
        maximum="1",
    ),
    PolicyField(
        key="per_token_cap_usd",
        label="Per-token exposure cap",
        unit="USD",
        kind="decimal",
        description=(
            "The largest USD value one token may reach, including the proposed buy. "
            "Reduce-only sells are never blocked by it."
        ),
        minimum="0",
        maximum="1000000",
    ),
    PolicyField(
        key="max_slippage_bps",
        label="Maximum slippage",
        unit="bps",
        kind="integer",
        description=(
            "The worst execution cost a new buy may accept, in basis points. "
            "A sell is never blocked by it."
        ),
        minimum="0",
        maximum="10000",
    ),
)

POLICY_FIELD_KEYS: tuple[str, ...] = tuple(field.key for field in POLICY_FIELDS)


class PolicyError(ValueError):
    """A refused policy read or write. ``code`` is the stable reason the API returns."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PolicyLimits:
    """The five sizing fields of one profile, independent of any deployment setting."""

    min_order_usd: Decimal
    max_order_usd: Decimal
    max_order_pct_of_equity: Decimal
    per_token_cap_usd: Decimal
    max_slippage_bps: int


@dataclass(frozen=True)
class SizingPreview:
    """The server's verdict on one proposed size under one profile."""

    allowed: bool
    profile: str
    amount_usd: Decimal
    equity_usd: Decimal | None
    limits: PolicyLimits
    percent_cap_usd: Decimal | None
    effective_max_usd: Decimal
    reasons: tuple[RiskReason, ...]


def profile_for(name: str | None) -> TradeRiskProfile | None:
    """Resolve a declared profile name. ``None`` means no profile is configured."""
    if name is None:
        return None
    if name not in PROFILE_ORDER:
        raise PolicyError("PROFILE_UNKNOWN", f"unknown risk profile {name!r}")
    return TRADE_RISK_PROFILES[name]


def limits_for(profile: TradeRiskProfile) -> PolicyLimits:
    return PolicyLimits(
        min_order_usd=profile.min_order_usd,
        max_order_usd=profile.max_order_usd,
        max_order_pct_of_equity=profile.max_order_pct_of_equity,
        per_token_cap_usd=profile.per_token_cap_usd,
        max_slippage_bps=profile.max_slippage_bps,
    )


def risk_limits_for(profile: TradeRiskProfile) -> RiskLimits:
    """A ``RiskLimits`` carrying the profile's sizing fields for the shared resolver."""
    return RiskLimits(
        min_order_usd=profile.min_order_usd,
        max_order_usd=profile.max_order_usd,
        max_order_pct_of_equity=profile.max_order_pct_of_equity,
        per_token_cap_usd=profile.per_token_cap_usd,
        max_slippage_bps=profile.max_slippage_bps,
    )


def field_value(profile: TradeRiskProfile, key: str) -> str | int:
    if key == "min_order_usd":
        return str(profile.min_order_usd)
    if key == "max_order_usd":
        return str(profile.max_order_usd)
    if key == "max_order_pct_of_equity":
        return str(profile.max_order_pct_of_equity)
    if key == "per_token_cap_usd":
        return str(profile.per_token_cap_usd)
    if key == "max_slippage_bps":
        return profile.max_slippage_bps
    raise PolicyError("POLICY_FIELD_UNKNOWN", f"unknown policy field {key!r}")


def profile_fields(profile: TradeRiskProfile) -> list[dict[str, object]]:
    """Every field with its value, range and description, in registry order."""
    rows: list[dict[str, object]] = []
    for field in POLICY_FIELDS:
        rows.append(
            {
                "key": field.key,
                "label": field.label,
                "unit": field.unit,
                "kind": field.kind,
                "description": field.description,
                "value": field_value(profile, field.key),
                "minimum": field.minimum,
                "maximum": field.maximum,
            }
        )
    return rows


def profile_document(profile: TradeRiskProfile) -> dict[str, object]:
    return {
        "name": profile.name,
        "label": PROFILE_LABELS.get(profile.name, profile.name),
        "description": PROFILE_DESCRIPTIONS.get(profile.name, ""),
        "fields": profile_fields(profile),
    }


def preview_size(
    profile_name: str | None,
    amount_usd: Decimal,
    equity_usd: Decimal | None,
) -> SizingPreview:
    """Check a proposed buy with the same resolver the order gate uses.

    ``effective_max_usd`` is the lower of the absolute ceiling and the percent-of-equity
    ceiling: the issue's "$100 or 5% of equity, whichever is lower" is exactly this number.
    When equity is unknown the percentage ceiling cannot be computed, so the absolute one
    is reported and the verdict carries ``EQUITY_UNKNOWN`` rather than pretending the size
    fits.
    """
    profile = profile_for(profile_name)
    if profile is None:
        raise PolicyError("PROFILE_UNKNOWN", "a sizing preview needs a risk profile")
    limits = risk_limits_for(profile)
    reasons = tuple(size_reasons(amount_usd, limits, equity_usd))
    percent_cap = None if equity_usd is None else equity_usd * profile.max_order_pct_of_equity
    effective_max = (
        profile.max_order_usd if percent_cap is None else min(profile.max_order_usd, percent_cap)
    )
    return SizingPreview(
        allowed=not reasons,
        profile=profile.name,
        amount_usd=amount_usd,
        equity_usd=equity_usd,
        limits=limits_for(profile),
        percent_cap_usd=percent_cap,
        effective_max_usd=effective_max,
        reasons=reasons,
    )


def validate_registry() -> None:
    """Fail closed at import if a profile's own bounds are not representable.

    The issue requires the UI to validate ``min <= max`` and ``0 <= percent <= 100``. The
    registry is the single source of those numbers, so a bad profile is a boot error, not
    a value a page might render and an order gate might silently misapply.
    """
    for name in PROFILE_ORDER:
        profile = TRADE_RISK_PROFILES[name]
        if profile.min_order_usd <= 0:
            raise PolicyError("POLICY_INVALID", f"{name}: min_order_usd must be positive")
        if profile.min_order_usd > profile.max_order_usd:
            raise PolicyError("POLICY_INVALID", f"{name}: min_order_usd exceeds max_order_usd")
        if not Decimal("0") <= profile.max_order_pct_of_equity <= Decimal("1"):
            raise PolicyError(
                "POLICY_INVALID",
                f"{name}: max_order_pct_of_equity must be between 0 and 1",
            )
        if profile.per_token_cap_usd <= 0:
            raise PolicyError("POLICY_INVALID", f"{name}: per_token_cap_usd must be positive")
        if not 0 <= profile.max_slippage_bps <= 10000:
            raise PolicyError(
                "POLICY_INVALID", f"{name}: max_slippage_bps must be between 0 and 10000"
            )


validate_registry()
