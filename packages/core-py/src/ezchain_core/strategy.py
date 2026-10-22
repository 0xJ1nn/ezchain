"""Declarative, user-defined strategies. No arbitrary code execution.

A strategy is a row of typed parameters plus one of four fixed rule kinds. There is no
plugin loading and no ``eval``: the same inputs always produce the same decision, so every
run can be replayed from the persisted snapshot and position.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from .types import StrategyKind, TokenSnapshot

Action = Literal["buy", "sell", "hold"]
Lookback = Literal["5m", "1h", "6h", "24h"]

SUPPORTED_LOOKBACKS: tuple[Lookback, ...] = ("5m", "1h", "6h", "24h")

DEFAULT_ORDER_USD = Decimal("100")
DEFAULT_MAX_POSITION_USD = Decimal("500")
DEFAULT_INTERVAL_MINUTES = 60
DEFAULT_TAKE_PROFIT_PCT = Decimal("20")
DEFAULT_STOP_LOSS_PCT = Decimal("8")
DEFAULT_ENTRY_CHANGE_PCT = Decimal("5")
DEFAULT_EXIT_CHANGE_PCT = Decimal("-3")
MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 10_080


class StrategyValidationError(ValueError):
    """A user-supplied parameter is missing or outside its typed range."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _decimal(params: Mapping[str, Any], key: str, default: Decimal | None = None) -> Decimal:
    raw = params.get(key)
    if raw is None:
        if default is None:
            raise StrategyValidationError("STRATEGY_PARAM_MISSING", f"{key} is required")
        return default
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str, Decimal)):
        raise StrategyValidationError(
            "STRATEGY_PARAM_TYPE", f"{key} must be a number, got {type(raw).__name__}"
        )
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise StrategyValidationError("STRATEGY_PARAM_TYPE", f"{key} must be a number") from exc
    if not value.is_finite():
        raise StrategyValidationError("STRATEGY_PARAM_TYPE", f"{key} must be finite")
    return value


def _positive(params: Mapping[str, Any], key: str, default: Decimal) -> Decimal:
    value = _decimal(params, key, default)
    if value <= 0:
        raise StrategyValidationError("STRATEGY_PARAM_RANGE", f"{key} must be > 0")
    return value


def _required_positive(params: Mapping[str, Any], key: str) -> Decimal:
    value = _decimal(params, key)
    if value <= 0:
        raise StrategyValidationError("STRATEGY_PARAM_RANGE", f"{key} must be > 0")
    return value


def _interval(params: Mapping[str, Any]) -> int:
    value = _decimal(params, "interval_minutes", Decimal(DEFAULT_INTERVAL_MINUTES))
    if value != value.to_integral_value():
        raise StrategyValidationError(
            "STRATEGY_PARAM_RANGE", "interval_minutes must be a whole number of minutes"
        )
    minutes = int(value)
    if not MIN_INTERVAL_MINUTES <= minutes <= MAX_INTERVAL_MINUTES:
        raise StrategyValidationError(
            "STRATEGY_PARAM_RANGE",
            f"interval_minutes must be between {MIN_INTERVAL_MINUTES} and {MAX_INTERVAL_MINUTES}",
        )
    return minutes


def _lookback(params: Mapping[str, Any]) -> Lookback:
    raw = params.get("lookback", "1h")
    if raw not in SUPPORTED_LOOKBACKS:
        supported = ", ".join(SUPPORTED_LOOKBACKS)
        raise StrategyValidationError(
            "STRATEGY_LOOKBACK_UNSUPPORTED", f"lookback must be one of: {supported}"
        )
    return raw  # type: ignore[no-any-return]


def _reject_unknown(params: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise StrategyValidationError(
            "STRATEGY_PARAM_UNKNOWN", f"unknown parameter(s): {', '.join(unknown)}"
        )


@dataclass(frozen=True)
class StrategyParams:
    """Fields shared by every kind. Subclasses add the kind-specific rules."""

    order_usd: Decimal
    interval_minutes: int
    max_position_usd: Decimal


@dataclass(frozen=True)
class MomentumParams(StrategyParams):
    lookback: Lookback
    entry_change_pct: Decimal
    exit_change_pct: Decimal
    take_profit_pct: Decimal
    stop_loss_pct: Decimal


@dataclass(frozen=True)
class DipParams(StrategyParams):
    lookback: Lookback
    entry_change_pct: Decimal
    take_profit_pct: Decimal
    stop_loss_pct: Decimal


@dataclass(frozen=True)
class DCAParams(StrategyParams):
    pass


@dataclass(frozen=True)
class ThresholdParams(StrategyParams):
    buy_below_usd: Decimal
    sell_above_usd: Decimal


AnyParams = MomentumParams | DipParams | DCAParams | ThresholdParams

_COMMON_KEYS = {"order_usd", "interval_minutes", "max_position_usd"}
_EXIT_KEYS = {"take_profit_pct", "stop_loss_pct"}


def validate_params(kind: StrategyKind, params: Mapping[str, Any] | None) -> AnyParams:
    """Parse and validate user params for one kind.

    Unknown keys are rejected: a typo in ``stop_loss_pct`` must not silently become an
    unbounded position.
    """
    if params is None:
        params = {}
    if not isinstance(params, Mapping):
        raise StrategyValidationError("STRATEGY_PARAM_TYPE", "params must be an object")

    order_usd = _positive(params, "order_usd", DEFAULT_ORDER_USD)
    interval = _interval(params)
    max_position = _positive(params, "max_position_usd", DEFAULT_MAX_POSITION_USD)

    if kind == "momentum":
        _reject_unknown(
            params,
            _COMMON_KEYS | _EXIT_KEYS | {"lookback", "entry_change_pct", "exit_change_pct"},
        )
        entry = _positive(params, "entry_change_pct", DEFAULT_ENTRY_CHANGE_PCT)
        exit_change = _decimal(params, "exit_change_pct", DEFAULT_EXIT_CHANGE_PCT)
        if exit_change >= 0:
            raise StrategyValidationError(
                "STRATEGY_PARAM_RANGE", "exit_change_pct must be < 0 for momentum"
            )
        return MomentumParams(
            order_usd=order_usd,
            interval_minutes=interval,
            max_position_usd=max_position,
            lookback=_lookback(params),
            entry_change_pct=entry,
            exit_change_pct=exit_change,
            take_profit_pct=_positive(params, "take_profit_pct", DEFAULT_TAKE_PROFIT_PCT),
            stop_loss_pct=_positive(params, "stop_loss_pct", DEFAULT_STOP_LOSS_PCT),
        )

    if kind == "dip":
        _reject_unknown(
            params,
            _COMMON_KEYS | _EXIT_KEYS | {"lookback", "entry_change_pct"},
        )
        return DipParams(
            order_usd=order_usd,
            interval_minutes=interval,
            max_position_usd=max_position,
            lookback=_lookback(params),
            entry_change_pct=_positive(params, "entry_change_pct", DEFAULT_ENTRY_CHANGE_PCT),
            take_profit_pct=_positive(params, "take_profit_pct", DEFAULT_TAKE_PROFIT_PCT),
            stop_loss_pct=_positive(params, "stop_loss_pct", DEFAULT_STOP_LOSS_PCT),
        )

    if kind == "dca":
        _reject_unknown(params, _COMMON_KEYS)
        return DCAParams(
            order_usd=order_usd,
            interval_minutes=interval,
            max_position_usd=max_position,
        )

    if kind == "threshold":
        _reject_unknown(params, _COMMON_KEYS | {"buy_below_usd", "sell_above_usd"})
        buy_below = _required_positive(params, "buy_below_usd")
        sell_above = _required_positive(params, "sell_above_usd")
        if sell_above <= buy_below:
            raise StrategyValidationError(
                "STRATEGY_PARAM_RANGE", "sell_above_usd must be > buy_below_usd"
            )
        return ThresholdParams(
            order_usd=order_usd,
            interval_minutes=interval,
            max_position_usd=max_position,
            buy_below_usd=buy_below,
            sell_above_usd=sell_above,
        )

    raise StrategyValidationError("STRATEGY_KIND_UNSUPPORTED", f"unsupported kind: {kind}")


def interval_minutes(kind: StrategyKind, params: Mapping[str, Any] | None) -> int:
    """The cooldown gate for one strategy. Raises on invalid params."""
    return validate_params(kind, params).interval_minutes


@dataclass(frozen=True)
class StrategySpec:
    id: str
    name: str
    chain: str
    token_address: str
    token_symbol: str
    kind: StrategyKind
    params: Mapping[str, Any]
    status: str = "active"
    last_run_at: datetime | None = None

    def number(self, key: str, default: Decimal | None = None) -> Decimal | None:
        raw = self.params.get(key)
        if raw is None:
            return default
        return Decimal(str(raw))

    @property
    def typed_params(self) -> AnyParams:
        return validate_params(self.kind, self.params)


@dataclass(frozen=True)
class StrategyDecision:
    action: Action
    amount_usd: Decimal
    reason: str
    inputs: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "amount_usd": str(self.amount_usd),
            "reason": self.reason,
            "inputs": self.inputs,
        }


def change_for_lookback(snapshot: TokenSnapshot, lookback: str) -> Decimal | None:
    mapping = {
        "5m": snapshot.price_change_m5,
        "1h": snapshot.price_change_h1,
        "6h": snapshot.price_change_h6,
        "24h": snapshot.price_change_h24,
    }
    return mapping.get(lookback)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _minutes_since(spec: StrategySpec, now: datetime | None) -> Decimal | None:
    if spec.last_run_at is None or now is None:
        return None
    elapsed = _aware(now) - _aware(spec.last_run_at)
    return Decimal(str(elapsed.total_seconds())) / 60


def _buy(
    params: StrategyParams,
    decision_inputs: dict[str, Any],
    reason: str,
    *,
    price: Decimal,
    position_quantity: Decimal,
) -> StrategyDecision:
    headroom = params.max_position_usd - position_quantity * price
    if headroom <= 0:
        return StrategyDecision("hold", Decimal(0), "MAX_POSITION", decision_inputs)
    amount = min(params.order_usd, headroom)
    return StrategyDecision("buy", amount, reason, decision_inputs)


def _sell(
    decision_inputs: dict[str, Any],
    reason: str,
    *,
    price: Decimal,
    position_quantity: Decimal,
) -> StrategyDecision:
    amount = position_quantity * price
    if amount <= 0:
        return StrategyDecision("hold", Decimal(0), "NO_POSITION", decision_inputs)
    return StrategyDecision("sell", amount, reason, decision_inputs)


def evaluate_strategy(
    spec: StrategySpec,
    snapshot: TokenSnapshot,
    *,
    position_quantity: Decimal = Decimal(0),
    position_avg_cost_usd: Decimal = Decimal(0),
    now: datetime | None = None,
) -> StrategyDecision:
    """Evaluate one strategy against one snapshot.

    The caller is responsible for the interval/cooldown gate and for persisting the
    decision. This function is pure so it can be replayed from persisted inputs.
    ``amount_usd`` for a sell is the position's full mark value: an exit leaves no dust to
    be sold again on the next tick.
    """
    if spec.status != "active":
        return StrategyDecision("hold", Decimal(0), "PAUSED", {})

    price = snapshot.price_usd
    if price is None:
        return StrategyDecision("hold", Decimal(0), "PRICE_UNKNOWN", {})
    if snapshot.stale:
        return StrategyDecision("hold", Decimal(0), "MARK_STALE", {})

    params = spec.typed_params
    position_value = position_quantity * price

    unrealized_pct: Decimal | None = None
    if position_quantity > 0 and position_avg_cost_usd > 0:
        unrealized_pct = (price / position_avg_cost_usd - 1) * 100

    decision_inputs: dict[str, Any] = {
        "kind": spec.kind,
        "price_usd": str(price),
        "position_quantity": str(position_quantity),
        "position_value_usd": str(position_value),
        "position_avg_cost_usd": str(position_avg_cost_usd),
        "unrealized_pct": None if unrealized_pct is None else str(unrealized_pct),
        "interval_minutes": params.interval_minutes,
    }

    if isinstance(params, DCAParams):
        elapsed = _minutes_since(spec, now)
        if elapsed is not None:
            decision_inputs["minutes_since_last_run"] = str(elapsed)
            if elapsed < params.interval_minutes:
                return StrategyDecision("hold", Decimal(0), "DCA_INTERVAL", decision_inputs)
        return _buy(
            params,
            decision_inputs,
            "DCA_BUY",
            price=price,
            position_quantity=position_quantity,
        )

    if isinstance(params, ThresholdParams):
        decision_inputs["buy_below_usd"] = str(params.buy_below_usd)
        decision_inputs["sell_above_usd"] = str(params.sell_above_usd)
        if position_quantity == 0 and price <= params.buy_below_usd:
            return _buy(
                params,
                decision_inputs,
                "THRESHOLD_BUY",
                price=price,
                position_quantity=position_quantity,
            )
        if position_quantity > 0 and price >= params.sell_above_usd:
            return _sell(
                decision_inputs,
                "THRESHOLD_SELL",
                price=price,
                position_quantity=position_quantity,
            )
        return StrategyDecision("hold", Decimal(0), "THRESHOLD_WAIT", decision_inputs)

    change = change_for_lookback(snapshot, params.lookback)
    decision_inputs["lookback"] = params.lookback
    decision_inputs["change_pct"] = None if change is None else str(change)
    if change is None:
        return StrategyDecision("hold", Decimal(0), "CHANGE_UNKNOWN", decision_inputs)

    if position_quantity > 0 and unrealized_pct is not None:
        if unrealized_pct >= params.take_profit_pct:
            return _sell(
                decision_inputs,
                "TAKE_PROFIT",
                price=price,
                position_quantity=position_quantity,
            )
        if unrealized_pct <= -params.stop_loss_pct:
            return _sell(
                decision_inputs,
                "STOP_LOSS",
                price=price,
                position_quantity=position_quantity,
            )

    if isinstance(params, MomentumParams):
        if position_quantity == 0 and change >= params.entry_change_pct:
            return _buy(
                params,
                decision_inputs,
                "MOMENTUM_ENTRY",
                price=price,
                position_quantity=position_quantity,
            )
        if position_quantity > 0 and change <= params.exit_change_pct:
            return _sell(
                decision_inputs,
                "MOMENTUM_EXIT",
                price=price,
                position_quantity=position_quantity,
            )
        return StrategyDecision("hold", Decimal(0), "MOMENTUM_WAIT", decision_inputs)

    if isinstance(params, DipParams):
        if position_quantity == 0 and change <= -params.entry_change_pct:
            return _buy(
                params,
                decision_inputs,
                "DIP_ENTRY",
                price=price,
                position_quantity=position_quantity,
            )
        return StrategyDecision("hold", Decimal(0), "DIP_WAIT", decision_inputs)

    return StrategyDecision("hold", Decimal(0), "UNKNOWN_KIND", decision_inputs)
