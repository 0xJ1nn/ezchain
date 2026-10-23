"""Persisted trade-sizing policy: defaults, validation and one settings row.

The config module owns the default numbers (CONTRACT I7); this service owns the one
persisted override. A malformed stored document never becomes a permissive policy: the
reader falls back to the config defaults and says so, so a bad row cannot silently size
an order.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any, Literal

from sqlalchemy.orm import Session

from ezchain_core import (
    PROFILE_NAMES,
    TradeSizeLimits,
    TradeSizePolicy,
    TradeSizeResolution,
    resolve_trade_size,
    validate_trade_policy,
)

from ..config import Settings
from ..models import AppSettingRow, now

logger = logging.getLogger(__name__)

TRADE_POLICY_KEY = "trade_policy"
ORDER_CENT = Decimal("0.01")
POLICY_SOURCE_DEFAULT: Literal["default"] = "default"
POLICY_SOURCE_PERSISTED: Literal["persisted"] = "persisted"
PolicySource = Literal["default", "persisted"]


@dataclass(frozen=True)
class TradePolicyRecord:
    policy: TradeSizePolicy
    source: PolicySource
    updated_at: datetime | None = None
    updated_by: str | None = None


def _decimal(value: object, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} is not a decimal") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field} must be finite")
    return parsed


def _limits_to_dict(limits: TradeSizeLimits) -> dict[str, str | None]:
    return {
        "min_amount_usd": str(limits.min_amount_usd),
        "min_pct_equity": str(limits.min_pct_equity),
        "max_amount_usd": None if limits.max_amount_usd is None else str(limits.max_amount_usd),
        "max_pct_equity": None if limits.max_pct_equity is None else str(limits.max_pct_equity),
    }


def policy_to_dict(policy: TradeSizePolicy) -> dict[str, Any]:
    """The exact JSON payload persisted in ``app_settings.value``."""
    return {
        "default_profile": policy.default_profile,
        "min_amount_usd": str(policy.limits.min_amount_usd),
        "min_pct_equity": str(policy.limits.min_pct_equity),
        "max_amount_usd": None
        if policy.limits.max_amount_usd is None
        else str(policy.limits.max_amount_usd),
        "max_pct_equity": None
        if policy.limits.max_pct_equity is None
        else str(policy.limits.max_pct_equity),
        "profiles": {name: _limits_to_dict(limits) for name, limits in policy.profiles.items()},
    }


def _limits_from_dict(value: object) -> TradeSizeLimits:
    if not isinstance(value, dict):
        raise ValueError("size limits must be an object")
    payload = value

    def required(field: str) -> Decimal:
        if field not in payload or payload[field] is None or payload[field] == "":
            raise ValueError(f"{field} is missing")
        return _decimal(payload[field], field)

    def optional(field: str) -> Decimal | None:
        raw = payload.get(field)
        if raw is None or raw == "":
            return None
        return _decimal(raw, field)

    return TradeSizeLimits(
        min_amount_usd=required("min_amount_usd"),
        min_pct_equity=required("min_pct_equity"),
        max_amount_usd=optional("max_amount_usd"),
        max_pct_equity=optional("max_pct_equity"),
    )


def policy_from_dict(value: object) -> TradeSizePolicy:
    """Parse a stored document, then reject it if any invariant does not hold.

    Every field is required: a partial document must not become a more permissive
    policy through an implicit zero. The caller falls back to the config defaults
    when this raises.
    """
    if not isinstance(value, dict):
        raise ValueError("trade policy must be an object")
    profiles_raw = value.get("profiles")
    if not isinstance(profiles_raw, dict):
        raise ValueError("trade policy profiles must be an object")
    profiles: dict[str, TradeSizeLimits] = {}
    for name in PROFILE_NAMES:
        if name not in profiles_raw:
            raise ValueError(f"trade policy profile {name!r} is missing")
        profiles[name] = _limits_from_dict(profiles_raw[name])
    if set(profiles_raw) - set(PROFILE_NAMES):
        raise ValueError("trade policy has an unknown profile")

    default_profile = value.get("default_profile")
    if not isinstance(default_profile, str) or not default_profile:
        raise ValueError("trade policy default_profile is missing")
    policy = TradeSizePolicy(
        limits=_limits_from_dict(value),
        profiles=profiles,
        default_profile=default_profile,
    )
    errors = validate_trade_policy(policy)
    if errors:
        raise ValueError("invalid trade policy: " + ", ".join(errors))
    return policy


def default_policy(settings: Settings) -> TradeSizePolicy:
    return settings.trade_size_policy


def load_trade_policy(session: Session, settings: Settings) -> TradePolicyRecord:
    """Read the persisted policy, or the config default with an explicit source."""
    row = session.get(AppSettingRow, TRADE_POLICY_KEY)
    if row is None:
        return TradePolicyRecord(policy=default_policy(settings), source=POLICY_SOURCE_DEFAULT)
    try:
        policy = policy_from_dict(row.value)
    except ValueError as exc:
        logger.warning("stored trade policy is invalid (%s); using config defaults", exc)
        return TradePolicyRecord(
            policy=default_policy(settings),
            source=POLICY_SOURCE_DEFAULT,
            updated_at=_aware(row.updated_at),
            updated_by=row.updated_by,
        )
    return TradePolicyRecord(
        policy=policy,
        source=POLICY_SOURCE_PERSISTED,
        updated_at=_aware(row.updated_at),
        updated_by=row.updated_by,
    )


def save_trade_policy(
    session: Session,
    policy: TradeSizePolicy,
    *,
    updated_by: str | None,
) -> TradePolicyRecord:
    """Validate, then upsert one row. Settings are never written to the ledger."""
    errors = validate_trade_policy(policy)
    if errors:
        raise ValueError("invalid trade policy: " + ", ".join(errors))
    row = session.get(AppSettingRow, TRADE_POLICY_KEY)
    stamp = now()
    if row is None:
        row = AppSettingRow(
            key=TRADE_POLICY_KEY,
            value=policy_to_dict(policy),
            updated_by=updated_by,
            updated_at=stamp,
        )
        session.add(row)
    else:
        row.value = policy_to_dict(policy)
        row.updated_by = updated_by
        row.updated_at = stamp
    session.commit()
    return TradePolicyRecord(
        policy=policy,
        source=POLICY_SOURCE_PERSISTED,
        updated_at=stamp,
        updated_by=updated_by,
    )


def resolve_order_size(
    *,
    equity_usd: Decimal | None,
    record: TradePolicyRecord,
    profile: str | None,
    manual_amount_usd: Decimal | None,
) -> TradeSizeResolution:
    return resolve_trade_size(
        equity_usd=equity_usd,
        policy=record.policy,
        profile=profile,
        manual_amount_usd=manual_amount_usd,
    )


def quantize_resolution(resolution: TradeSizeResolution) -> TradeSizeResolution:
    """Carry a resolution to the order boundary without resizing a manual amount.

    A derived size is quantized once, down to cents, then re-checked against the same
    snapshot. Rounding down can never cross an upper bound; if it crosses the lower
    bound the refusal code is the one the order path would store. Manual amounts are
    already cents from the request schema and are returned untouched.
    """
    if resolution.amount_usd is None or resolution.manual:
        return resolution
    try:
        amount = resolution.amount_usd.quantize(ORDER_CENT, rounding=ROUND_DOWN)
    except InvalidOperation:
        return replace(resolution, amount_usd=None, reason="SIZE_ABOVE_MAX")
    limits = resolution.limits
    if amount <= 0 or (limits is not None and amount < limits.min_amount_usd):
        return replace(resolution, amount_usd=None, reason="SIZE_BELOW_MIN")
    if limits is not None and limits.max_amount_usd is not None and amount > limits.max_amount_usd:
        return replace(resolution, amount_usd=None, reason="SIZE_ABOVE_MAX")
    return replace(resolution, amount_usd=amount)


def previews(policy: TradeSizePolicy, equity_usd: Decimal | None) -> dict[str, TradeSizeResolution]:
    """One server-resolved automatic-size preview per profile."""
    return {
        name: quantize_resolution(
            resolve_trade_size(equity_usd=equity_usd, policy=policy, profile=name)
        )
        for name in PROFILE_NAMES
    }


def draft_policy(
    policy: TradeSizePolicy,
    *,
    profile: str,
    limits: TradeSizeLimits,
    envelope: TradeSizeLimits | None = None,
) -> TradeSizePolicy:
    """A full draft policy for the read-only preview route.

    The caller passes the unsaved envelope and profile values so the preview is resolved
    against exactly what the form would save, never a stale persisted bound.
    """
    if profile not in PROFILE_NAMES:
        raise ValueError("unknown risk profile")
    return replace(
        policy,
        limits=policy.limits if envelope is None else envelope,
        profiles={**policy.profiles, profile: limits},
    )


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
