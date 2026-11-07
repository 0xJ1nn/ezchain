"""Pure protection rule evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal


class ProtectionValidationError(ValueError):
    """Raised when protection parameters are malformed or incomplete."""


@dataclass(frozen=True)
class TrailingState:
    peak: Decimal | None = None
    activated: bool = False
    rung_index: int = 0


@dataclass(frozen=True)
class ProtectionDecision:
    action: Literal["sell", "hold"]
    reason: str
    sell_fraction: Decimal = Decimal("1")
    new_peak: Decimal | None = None
    new_activated: bool = False
    new_rung_index: int = 0


_STOP_LOSS_KEYS = {"loss_pct", "stop_price_usd", "sell_fraction"}
_TRAILING_KEYS = {"activation_pct", "giveback_pct", "giveback_price_usd", "sell_fraction"}
_TAKE_PROFIT_KEYS = {"profit_pct", "sell_fraction", "ladder"}


def _as_decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def validate_protection_params(kind: str, params: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(params, dict):
        raise ProtectionValidationError("params must be an object")
    kind = str(kind)
    if kind == "stop_loss":
        allowed = _STOP_LOSS_KEYS
    elif kind == "trailing_stop":
        allowed = _TRAILING_KEYS
    elif kind == "take_profit":
        allowed = _TAKE_PROFIT_KEYS
    else:
        raise ProtectionValidationError(f"unknown protection kind: {kind}")

    unknown = set(params) - allowed
    if unknown:
        raise ProtectionValidationError(f"unknown protection keys: {sorted(unknown)}")

    if kind == "stop_loss":
        if not ({"loss_pct", "stop_price_usd"} & set(params)):
            raise ProtectionValidationError("stop_loss requires loss_pct or stop_price_usd")
    elif kind == "trailing_stop":
        if "activation_pct" not in params:
            raise ProtectionValidationError("trailing_stop requires activation_pct")
    elif kind == "take_profit":
        if not ({"profit_pct", "ladder"} & set(params)):
            raise ProtectionValidationError("take_profit requires profit_pct or ladder")

    for key in (
        "loss_pct",
        "stop_price_usd",
        "sell_fraction",
        "activation_pct",
        "giveback_pct",
        "giveback_price_usd",
        "profit_pct",
    ):
        if key in params:
            try:
                Decimal(str(params[key]))
            except Exception as exc:
                raise ProtectionValidationError(f"{key} must be numeric") from exc
    if "ladder" in params:
        ladder = params["ladder"]
        if not isinstance(ladder, list) or not ladder:
            raise ProtectionValidationError("ladder must be a non-empty list")
        for rung in ladder:
            if not isinstance(rung, dict):
                raise ProtectionValidationError("ladder rungs must be objects")
            if "profit_pct" not in rung or "fraction" not in rung:
                raise ProtectionValidationError("ladder rung requires profit_pct and fraction")
            try:
                Decimal(str(rung["profit_pct"]))
                Decimal(str(rung["fraction"]))
            except Exception as exc:
                raise ProtectionValidationError("ladder values must be numeric") from exc

    return dict(params)


def resolve_profile_params(
    *,
    global_defaults: dict[str, Any] | None = None,
    chain_defaults: dict[str, Any] | None = None,
    explicit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for source in (global_defaults or {}, chain_defaults or {}, explicit or {}):
        for key, value in (source or {}).items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                merged = dict(result[key])
                merged.update(value)
                result[key] = merged
            else:
                result[key] = value
    return result


def evaluate_protection(
    *,
    kind: str,
    params: dict[str, Any] | None,
    avg_cost_usd: Decimal,
    mark_usd: Decimal | None,
    held_qty: Decimal,
    trailing: TrailingState,
) -> ProtectionDecision:
    params = params or {}
    peak = trailing.peak
    activated = trailing.activated
    rung_index = trailing.rung_index

    if mark_usd is None:
        return ProtectionDecision(
            action="hold",
            reason="PRICE_UNKNOWN",
            sell_fraction=Decimal("1"),
            new_peak=peak,
            new_activated=activated,
            new_rung_index=rung_index,
        )

    mark = Decimal(str(mark_usd))
    avg = Decimal(str(avg_cost_usd))

    if kind == "stop_loss":
        sell_fraction = Decimal(str(params.get("sell_fraction", "1")))
        triggered = False
        if "stop_price_usd" in params:
            if mark <= _as_decimal(params["stop_price_usd"]):
                triggered = True
        if "loss_pct" in params:
            threshold = avg * (Decimal("1") - _as_decimal(params["loss_pct"]) / Decimal("100"))
            if mark <= threshold:
                triggered = True
        if triggered:
            return ProtectionDecision(
                action="sell",
                reason="STOP_LOSS",
                sell_fraction=sell_fraction,
                new_peak=peak,
                new_activated=activated,
                new_rung_index=rung_index,
            )
        return ProtectionDecision(
            action="hold",
            reason="HOLD",
            sell_fraction=sell_fraction,
            new_peak=peak,
            new_activated=activated,
            new_rung_index=rung_index,
        )

    if kind == "take_profit":
        if "ladder" in params:
            ladder = params["ladder"]
            sell_fraction = Decimal("1")
            if rung_index >= len(ladder):
                return ProtectionDecision(
                    action="hold",
                    reason="TAKE_PROFIT_COMPLETE",
                    sell_fraction=sell_fraction,
                    new_peak=peak,
                    new_activated=activated,
                    new_rung_index=rung_index,
                )
            rung = ladder[rung_index]
            threshold = avg * (Decimal("1") + _as_decimal(rung["profit_pct"]) / Decimal("100"))
            if mark >= threshold:
                return ProtectionDecision(
                    action="sell",
                    reason="TAKE_PROFIT",
                    sell_fraction=_as_decimal(rung["fraction"]),
                    new_peak=peak,
                    new_activated=activated,
                    new_rung_index=rung_index + 1,
                )
            return ProtectionDecision(
                action="hold",
                reason="HOLD",
                sell_fraction=_as_decimal(rung["fraction"]),
                new_peak=peak,
                new_activated=activated,
                new_rung_index=rung_index,
            )
        profit_pct = _as_decimal(params.get("profit_pct", "0"))
        sell_fraction = Decimal(str(params.get("sell_fraction", "1")))
        threshold = avg * (Decimal("1") + profit_pct / Decimal("100"))
        if mark >= threshold:
            return ProtectionDecision(
                action="sell",
                reason="TAKE_PROFIT",
                sell_fraction=sell_fraction,
                new_peak=peak,
                new_activated=activated,
                new_rung_index=rung_index,
            )
        return ProtectionDecision(
            action="hold",
            reason="HOLD",
            sell_fraction=sell_fraction,
            new_peak=peak,
            new_activated=activated,
            new_rung_index=rung_index,
        )

    if kind == "trailing_stop":
        sell_fraction = Decimal(str(params.get("sell_fraction", "1")))
        activation_pct = _as_decimal(params.get("activation_pct", "0"))
        activation_price = avg * (Decimal("1") + activation_pct / Decimal("100"))

        if not activated:
            if mark >= activation_price:
                return ProtectionDecision(
                    action="hold",
                    reason="TRAILING_ACTIVATED",
                    sell_fraction=sell_fraction,
                    new_peak=mark,
                    new_activated=True,
                    new_rung_index=rung_index,
                )
            return ProtectionDecision(
                action="hold",
                reason="TRAILING_NOT_ACTIVATED",
                sell_fraction=sell_fraction,
                new_peak=peak,
                new_activated=False,
                new_rung_index=rung_index,
            )

        new_peak = peak if (peak is not None and peak >= mark) else mark
        triggered = False
        if "giveback_pct" in params and peak is not None:
            threshold = peak * (Decimal("1") - _as_decimal(params["giveback_pct"]) / Decimal("100"))
            if mark <= threshold:
                triggered = True
        if "giveback_price_usd" in params and peak is not None:
            if peak - mark >= _as_decimal(params["giveback_price_usd"]):
                triggered = True
        if triggered:
            return ProtectionDecision(
                action="sell",
                reason="TRAILING_STOP",
                sell_fraction=sell_fraction,
                new_peak=new_peak,
                new_activated=True,
                new_rung_index=rung_index,
            )
        return ProtectionDecision(
            action="hold",
            reason="HOLD",
            sell_fraction=sell_fraction,
            new_peak=new_peak,
            new_activated=True,
            new_rung_index=rung_index,
        )

    raise ProtectionValidationError(f"unknown protection kind: {kind}")
