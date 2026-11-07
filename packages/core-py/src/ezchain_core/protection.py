"""Position protection arms: pure evaluation for paper exits. No I/O, no ledger writes.

An arm protects one open position. The engine evaluates arms on ticks and fires the
same OrderExecutor a manual order uses, so the ledger journals the close exactly like
any other fill (CONTRACT I1/I2). This module only decides, from persisted inputs,
whether an arm should trigger. Unknown or stale marks never trigger (I3).

Kinds: ``stop_loss`` (price or percent below entry), ``trailing_stop`` (percent or
price distance below the peak, above-water only), ``take_profit`` (single or
laddered). Profiles carry reusable defaults; a per-chain profile overrides the
global one. A smart profile carries inspectable rule descriptions only: no magic,
no claims of alpha.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

ArmKind = Literal["stop_loss", "trailing_stop", "take_profit"]
ArmStatus = Literal["armed", "triggered", "cancelled", "expired"]
ProfileKind = Literal["manual", "smart"]

DEFAULT_STOP_LOSS_PCT = Decimal("8")
DEFAULT_TRAILING_PCT = Decimal("5")
DEFAULT_TAKE_PROFIT_PCT = Decimal("20")

_PROFILE_KEYS = {"stop_loss_pct", "trailing_pct", "take_profit_pct"}


class ProtectionValidationError(ValueError):
    """A user-supplied protection parameter is missing or outside its range."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _decimal(raw: Any, key: str) -> Decimal:
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str, Decimal)):
        raise ProtectionValidationError(
            "PROTECTION_PARAM_TYPE", f"{key} must be a number, got {type(raw).__name__}"
        )
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise ProtectionValidationError("PROTECTION_PARAM_TYPE", f"{key} must be a number") from exc
    if not value.is_finite():
        raise ProtectionValidationError("PROTECTION_PARAM_TYPE", f"{key} must be finite")
    return value


def _optional_decimal(params: Mapping[str, Any], key: str) -> Decimal | None:
    if key not in params or params[key] is None:
        return None
    return _decimal(params[key], key)


def _reject_unknown(params: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise ProtectionValidationError(
            "PROTECTION_PARAM_UNKNOWN", f"unknown parameter(s): {', '.join(unknown)}"
        )


def _positive(value: Decimal, key: str) -> Decimal:
    if value <= 0:
        raise ProtectionValidationError("PROTECTION_PARAM_RANGE", f"{key} must be > 0")
    return value


@dataclass(frozen=True)
class TakeProfitLevel:
    price_usd: Decimal | None
    gain_pct: Decimal | None
    fraction: Decimal

    def threshold(self, avg_cost_usd: Decimal) -> Decimal | None:
        if self.price_usd is not None:
            return self.price_usd
        if self.gain_pct is not None and avg_cost_usd > 0:
            return avg_cost_usd * (Decimal(1) + self.gain_pct / Decimal(100))
        return None


@dataclass(frozen=True)
class ArmParams:
    kind: ArmKind
    price_usd: Decimal | None
    loss_pct: Decimal | None
    gain_pct: Decimal | None
    trailing_pct: Decimal | None
    trailing_price_usd: Decimal | None
    sell_fraction: Decimal
    levels: tuple[TakeProfitLevel, ...]


def _parse_levels(raw: Any) -> tuple[TakeProfitLevel, ...]:
    if not isinstance(raw, (list, tuple)) or len(raw) == 0:
        raise ProtectionValidationError(
            "PROTECTION_PARAM_MISSING", "levels must be a non-empty list for a ladder"
        )
    levels: list[TakeProfitLevel] = []
    total = Decimal(0)
    basis: str | None = None
    last_threshold: Decimal | None = None
    for index, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            raise ProtectionValidationError(
                "PROTECTION_PARAM_TYPE", f"levels[{index}] must be an object"
            )
        _reject_unknown(entry, {"price_usd", "gain_pct", "fraction"})
        if "fraction" not in entry:
            raise ProtectionValidationError(
                "PROTECTION_PARAM_MISSING", f"levels[{index}].fraction is required"
            )
        fraction = _positive(_decimal(entry["fraction"], f"levels[{index}].fraction"), "fraction")
        if fraction > 1:
            raise ProtectionValidationError(
                "PROTECTION_PARAM_RANGE", f"levels[{index}].fraction must be <= 1"
            )
        price = _optional_decimal(entry, "price_usd")
        gain = _optional_decimal(entry, "gain_pct")
        if (price is None) == (gain is None):
            raise ProtectionValidationError(
                "PROTECTION_PARAM_MISSING",
                f"levels[{index}] needs exactly one of price_usd, gain_pct",
            )
        key = "price" if price is not None else "gain"
        if basis is None:
            basis = key
        elif basis != key:
            raise ProtectionValidationError(
                "PROTECTION_PARAM_RANGE", "ladder levels must share one price basis"
            )
        threshold = price if price is not None else gain
        assert threshold is not None
        _positive(threshold, f"levels[{index}] threshold")
        if last_threshold is not None and threshold <= last_threshold:
            raise ProtectionValidationError(
                "PROTECTION_PARAM_RANGE", "ladder levels must be strictly increasing"
            )
        last_threshold = threshold
        total += fraction
        levels.append(TakeProfitLevel(price_usd=price, gain_pct=gain, fraction=fraction))
    if total > 1:
        raise ProtectionValidationError(
            "PROTECTION_PARAM_RANGE", "ladder fractions must sum to <= 1"
        )
    return tuple(levels)


def validate_arm(kind: str, params: Mapping[str, Any] | None) -> ArmParams:
    """Parse and validate arm parameters. Unknown keys are rejected, as in strategy."""
    if params is None:
        params = {}
    if not isinstance(params, Mapping):
        raise ProtectionValidationError("PROTECTION_PARAM_TYPE", "params must be an object")
    if kind not in ("stop_loss", "trailing_stop", "take_profit"):
        raise ProtectionValidationError("PROTECTION_KIND_UNSUPPORTED", f"unsupported kind: {kind}")

    sell_fraction = Decimal(1)
    if "sell_fraction" in params and params["sell_fraction"] is not None:
        sell_fraction = _positive(
            _decimal(params["sell_fraction"], "sell_fraction"), "sell_fraction"
        )
        if sell_fraction > 1:
            raise ProtectionValidationError("PROTECTION_PARAM_RANGE", "sell_fraction must be <= 1")

    if kind == "stop_loss":
        _reject_unknown(params, {"price_usd", "loss_pct", "sell_fraction"})
        price = _optional_decimal(params, "price_usd")
        loss = _optional_decimal(params, "loss_pct")
        if (price is None) == (loss is None):
            raise ProtectionValidationError(
                "PROTECTION_PARAM_MISSING",
                "stop-loss needs exactly one of price_usd, loss_pct",
            )
        if price is not None:
            _positive(price, "price_usd")
        if loss is not None:
            _positive(loss, "loss_pct")
            if loss > 100:
                raise ProtectionValidationError("PROTECTION_PARAM_RANGE", "loss_pct must be <= 100")
        return ArmParams("stop_loss", price, loss, None, None, None, sell_fraction, ())

    if kind == "trailing_stop":
        _reject_unknown(params, {"trailing_pct", "trailing_price_usd", "sell_fraction"})
        pct = _optional_decimal(params, "trailing_pct")
        distance = _optional_decimal(params, "trailing_price_usd")
        if (pct is None) == (distance is None):
            raise ProtectionValidationError(
                "PROTECTION_PARAM_MISSING",
                "trailing stop needs exactly one of trailing_pct, trailing_price_usd",
            )
        if pct is not None:
            _positive(pct, "trailing_pct")
            if pct >= 100:
                raise ProtectionValidationError(
                    "PROTECTION_PARAM_RANGE", "trailing_pct must be < 100"
                )
        if distance is not None:
            _positive(distance, "trailing_price_usd")
        return ArmParams("trailing_stop", None, None, None, pct, distance, sell_fraction, ())

    # take_profit
    if "levels" in params and params["levels"] is not None:
        _reject_unknown(params, {"levels"})
        return ArmParams(
            "take_profit",
            None,
            None,
            None,
            None,
            None,
            Decimal(1),
            _parse_levels(params["levels"]),
        )
    _reject_unknown(params, {"price_usd", "gain_pct", "sell_fraction"})
    price = _optional_decimal(params, "price_usd")
    gain = _optional_decimal(params, "gain_pct")
    if (price is None) == (gain is None):
        raise ProtectionValidationError(
            "PROTECTION_PARAM_MISSING",
            "take-profit needs exactly one of price_usd, gain_pct (or levels)",
        )
    if price is not None:
        _positive(price, "price_usd")
    if gain is not None:
        _positive(gain, "gain_pct")
    return ArmParams("take_profit", price, None, gain, None, None, sell_fraction, ())


@dataclass(frozen=True)
class ProtectionArm:
    id: str
    chain: str
    token_address: str
    token_symbol: str
    kind: ArmKind
    status: ArmStatus
    params: ArmParams
    peak_usd: Decimal | None
    triggered_level_indexes: tuple[int, ...]
    triggered_at: datetime | None
    order_id: str | None
    expires_at: datetime | None
    profile_id: str | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class ProtectionProfile:
    id: str
    name: str
    chain: str | None
    kind: ProfileKind
    stop_loss_pct: Decimal | None
    trailing_pct: Decimal | None
    take_profit_pct: Decimal | None
    rules: tuple[str, ...]
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class ArmEvaluation:
    action: Literal["hold", "trigger"]
    reason: str
    sell_fraction: Decimal
    level_index: int | None
    peak_usd: Decimal | None
    inputs: dict[str, Any]


def validate_profile(
    name: str,
    chain: str | None,
    kind: str,
    params: Mapping[str, Any] | None,
    rules: Sequence[str] | None,
) -> tuple[Decimal | None, Decimal | None, Decimal | None, tuple[str, ...]]:
    """Validate profile fields; returns (stop_loss_pct, trailing_pct, take_profit_pct, rules)."""
    if not name or not name.strip() or len(name) > 120:
        raise ProtectionValidationError("PROFILE_PARAM_RANGE", "name is required (1-120 chars)")
    if chain is not None and (len(chain) < 2 or len(chain) > 40):
        raise ProtectionValidationError("PROFILE_PARAM_RANGE", "chain must be 2-40 chars")
    if kind not in ("manual", "smart"):
        raise ProtectionValidationError("PROFILE_KIND_UNSUPPORTED", f"unsupported kind: {kind}")
    if params is None:
        params = {}
    if not isinstance(params, Mapping):
        raise ProtectionValidationError("PROFILE_PARAM_TYPE", "params must be an object")
    _reject_unknown(params, _PROFILE_KEYS)
    parsed: dict[str, Decimal] = {}
    for key in _PROFILE_KEYS:
        value = _optional_decimal(params, key)
        if value is not None:
            _positive(value, key)
            parsed[key] = value
    if not parsed:
        raise ProtectionValidationError(
            "PROFILE_PARAM_MISSING", "a profile needs at least one default"
        )
    clean_rules: tuple[str, ...] = ()
    if rules is not None:
        if not all(isinstance(rule, str) for rule in rules):
            raise ProtectionValidationError("PROFILE_PARAM_TYPE", "rules must be strings")
        clean_rules = tuple(rule.strip() for rule in rules if rule.strip())
    if kind == "smart" and not clean_rules:
        raise ProtectionValidationError(
            "PROFILE_RULES_REQUIRED", "a smart profile must carry inspectable rules"
        )
    if kind == "manual" and clean_rules:
        raise ProtectionValidationError(
            "PROFILE_RULES_NOT_ALLOWED", "a manual profile carries no rules"
        )
    return (
        parsed.get("stop_loss_pct"),
        parsed.get("trailing_pct"),
        parsed.get("take_profit_pct"),
        clean_rules,
    )


def resolve_profile(profiles: Sequence[ProtectionProfile], chain: str) -> ProtectionProfile | None:
    """Per-chain profile wins; otherwise the newest global; otherwise none."""
    per_chain: list[ProtectionProfile] = []
    global_: list[ProtectionProfile] = []

    def _key(p: ProtectionProfile) -> datetime:
        stamp = p.updated_at or p.created_at
        return (
            stamp.replace(tzinfo=UTC)
            if stamp is not None and stamp.tzinfo is None
            else (stamp or datetime.min.replace(tzinfo=UTC))
        )

    for profile in profiles:
        if profile.chain is not None and profile.chain == chain:
            per_chain.append(profile)
        elif profile.chain is None:
            global_.append(profile)
    if per_chain:
        return max(per_chain, key=_key)
    if global_:
        return max(global_, key=_key)
    return None


def evaluate_arm(
    arm: ProtectionArm,
    *,
    price_usd: Decimal | None,
    position_quantity: Decimal,
    position_avg_cost_usd: Decimal,
    stale: bool = False,
    now: datetime | None = None,
) -> ArmEvaluation:
    """Pure trigger decision from persisted inputs. Never places an order."""
    inputs: dict[str, Any] = {
        "kind": arm.kind,
        "status": arm.status,
        "price_usd": None if price_usd is None else str(price_usd),
        "position_quantity": str(position_quantity),
        "position_avg_cost_usd": str(position_avg_cost_usd),
        "peak_usd": None if arm.peak_usd is None else str(arm.peak_usd),
    }
    if arm.status != "armed":
        return ArmEvaluation("hold", "NOT_ARMED", Decimal(0), None, arm.peak_usd, inputs)
    if arm.expires_at is not None and now is not None:
        expiry = arm.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        moment = now.replace(tzinfo=UTC) if now.tzinfo is None else now
        if moment >= expiry:
            return ArmEvaluation("hold", "EXPIRED", Decimal(0), None, arm.peak_usd, inputs)
    if price_usd is None:
        return ArmEvaluation("hold", "PRICE_UNKNOWN", Decimal(0), None, arm.peak_usd, inputs)
    if stale:
        return ArmEvaluation("hold", "MARK_STALE", Decimal(0), None, arm.peak_usd, inputs)
    if position_quantity <= 0:
        return ArmEvaluation("hold", "NO_POSITION", Decimal(0), None, arm.peak_usd, inputs)

    params = arm.params
    if params.kind == "stop_loss":
        if params.price_usd is not None:
            threshold = params.price_usd
        elif params.loss_pct is not None and position_avg_cost_usd > 0:
            threshold = position_avg_cost_usd * (Decimal(1) - params.loss_pct / Decimal(100))
        else:
            return ArmEvaluation("hold", "INVALID_ARM", Decimal(0), None, arm.peak_usd, inputs)
        inputs["trigger_price_usd"] = str(threshold)
        if price_usd <= threshold:
            return ArmEvaluation(
                "trigger", "STOP_LOSS", params.sell_fraction, None, arm.peak_usd, inputs
            )
        return ArmEvaluation("hold", "ARMED", Decimal(0), None, arm.peak_usd, inputs)

    if params.kind == "take_profit":
        if params.levels:
            for index, level in enumerate(params.levels):
                if index in arm.triggered_level_indexes:
                    continue
                level_threshold = level.threshold(position_avg_cost_usd)
                if level_threshold is None:
                    return ArmEvaluation(
                        "hold", "INVALID_ARM", Decimal(0), None, arm.peak_usd, inputs
                    )
                inputs[f"level_{index}_trigger_usd"] = str(level_threshold)
                if price_usd >= level_threshold:
                    return ArmEvaluation(
                        "trigger",
                        "TAKE_PROFIT_LEVEL",
                        level.fraction,
                        index,
                        arm.peak_usd,
                        inputs,
                    )
            return ArmEvaluation("hold", "ARMED", Decimal(0), None, arm.peak_usd, inputs)
        if params.price_usd is not None:
            threshold = params.price_usd
        elif params.gain_pct is not None and position_avg_cost_usd > 0:
            threshold = position_avg_cost_usd * (Decimal(1) + params.gain_pct / Decimal(100))
        else:
            return ArmEvaluation("hold", "INVALID_ARM", Decimal(0), None, arm.peak_usd, inputs)
        inputs["trigger_price_usd"] = str(threshold)
        if price_usd >= threshold:
            return ArmEvaluation(
                "trigger", "TAKE_PROFIT", params.sell_fraction, None, arm.peak_usd, inputs
            )
        return ArmEvaluation("hold", "ARMED", Decimal(0), None, arm.peak_usd, inputs)

    # trailing_stop: track only the above-water peak. Never above water means
    # no peak and no trigger, however far the price then falls (fairness).
    peak = arm.peak_usd
    above_water = price_usd > position_avg_cost_usd if position_avg_cost_usd > 0 else True
    if above_water:
        peak = price_usd if peak is None else max(peak, price_usd)
    inputs["peak_usd"] = None if peak is None else str(peak)
    if peak is None:
        return ArmEvaluation("hold", "ARMED", Decimal(0), None, None, inputs)
    if params.trailing_pct is not None:
        threshold = peak * (Decimal(1) - params.trailing_pct / Decimal(100))
    elif params.trailing_price_usd is not None:
        threshold = peak - params.trailing_price_usd
    else:
        return ArmEvaluation("hold", "INVALID_ARM", Decimal(0), None, peak, inputs)
    inputs["trigger_price_usd"] = str(threshold)
    if price_usd <= threshold:
        return ArmEvaluation("trigger", "TRAILING_STOP", params.sell_fraction, None, peak, inputs)
    return ArmEvaluation("hold", "ARMED", Decimal(0), None, peak, inputs)
