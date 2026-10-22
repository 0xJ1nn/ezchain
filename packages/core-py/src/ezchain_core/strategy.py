"""Declarative, user-defined strategies. No arbitrary code execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from .types import StrategyKind, TokenSnapshot

Action = Literal["buy", "sell", "hold"]


def _dec(value: Any, default: Decimal | None = None) -> Decimal | None:
    if value is None:
        return default
    return Decimal(str(value))


@dataclass(frozen=True)
class StrategySpec:
    id: str
    name: str
    chain: str
    token_address: str
    token_symbol: str
    kind: StrategyKind
    params: dict[str, Any]
    status: str = "active"
    last_run_at: datetime | None = None

    def number(self, key: str, default: Decimal | None = None) -> Decimal | None:
        return _dec(self.params.get(key), default)


@dataclass(frozen=True)
class StrategyDecision:
    action: Action
    amount_usd: Decimal
    reason: str
    inputs: dict[str, Any] = field(default_factory=dict)

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


def evaluate_strategy(
    spec: StrategySpec,
    snapshot: TokenSnapshot,
    *,
    position_quantity: Decimal = Decimal(0),
    position_avg_cost_usd: Decimal = Decimal(0),
    now: datetime | None = None,
) -> StrategyDecision:
    """Evaluate one strategy against one snapshot.

    The caller is responsible for the interval/cooldown gate. This function is pure so it
    can be replayed from persisted inputs.
    """
    if spec.status != "active":
        return StrategyDecision("hold", Decimal(0), "PAUSED", {})

    price = snapshot.price_usd
    if price is None:
        return StrategyDecision("hold", Decimal(0), "PRICE_UNKNOWN", {})
    if snapshot.stale:
        return StrategyDecision("hold", Decimal(0), "MARK_STALE", {})

    order_usd = spec.number("order_usd", Decimal("100")) or Decimal("100")
    take_profit = spec.number("take_profit_pct", Decimal("20")) or Decimal("20")
    stop_loss = spec.number("stop_loss_pct", Decimal("8")) or Decimal("8")
    max_position = spec.number("max_position_usd", Decimal("500")) or Decimal("500")

    unrealized_pct: Decimal | None = None
    if position_quantity > 0 and position_avg_cost_usd > 0:
        unrealized_pct = (price / position_avg_cost_usd - 1) * 100

    base_inputs = {
        "price_usd": str(price),
        "position_quantity": str(position_quantity),
        "unrealized_pct": None if unrealized_pct is None else str(unrealized_pct),
        "kind": spec.kind,
    }

    if spec.kind == "dca":
        interval = spec.number("interval_minutes", Decimal("60")) or Decimal("60")
        if spec.last_run_at is not None and now is not None:
            elapsed_minutes = Decimal((now - spec.last_run_at).total_seconds()) / 60
            if elapsed_minutes < interval:
                return StrategyDecision("hold", Decimal(0), "DCA_INTERVAL", base_inputs)
        if position_quantity > 0:
            current_value = position_quantity * price
            if current_value >= max_position:
                return StrategyDecision("hold", Decimal(0), "MAX_POSITION", base_inputs)
        return StrategyDecision("buy", order_usd, "DCA_BUY", base_inputs)

    if spec.kind == "threshold":
        buy_below = spec.number("buy_below_usd")
        sell_above = spec.number("sell_above_usd")
        if buy_below is not None and price <= buy_below and position_quantity == 0:
            return StrategyDecision("buy", order_usd, "THRESHOLD_BUY", base_inputs)
        if sell_above is not None and price >= sell_above and position_quantity > 0:
            return StrategyDecision("sell", order_usd, "THRESHOLD_SELL", base_inputs)
        return StrategyDecision("hold", Decimal(0), "THRESHOLD_WAIT", base_inputs)

    lookback = str(spec.params.get("lookback", "1h"))
    change = change_for_lookback(snapshot, lookback)
    if change is None:
        return StrategyDecision("hold", Decimal(0), "MARK_STALE", base_inputs)
    base_inputs["lookback"] = lookback
    base_inputs["change_pct"] = str(change)

    if position_quantity > 0 and unrealized_pct is not None:
        if unrealized_pct >= take_profit:
            return StrategyDecision("sell", order_usd, "TAKE_PROFIT", base_inputs)
        if unrealized_pct <= -stop_loss:
            return StrategyDecision("sell", order_usd, "STOP_LOSS", base_inputs)

    if spec.kind == "momentum":
        entry = spec.number("entry_change_pct", Decimal("5")) or Decimal("5")
        exit_change = spec.number("exit_change_pct", Decimal("-3")) or Decimal("-3")
        if position_quantity == 0 and change >= entry:
            return StrategyDecision("buy", order_usd, "MOMENTUM_ENTRY", base_inputs)
        if position_quantity > 0 and change <= exit_change:
            return StrategyDecision("sell", order_usd, "MOMENTUM_EXIT", base_inputs)
        return StrategyDecision("hold", Decimal(0), "MOMENTUM_WAIT", base_inputs)

    if spec.kind == "dip":
        entry_drop = spec.number("entry_change_pct", Decimal("5")) or Decimal("5")
        if position_quantity == 0 and change <= -entry_drop:
            return StrategyDecision("buy", order_usd, "DIP_ENTRY", base_inputs)
        if position_quantity > 0 and change >= (
            spec.number("exit_change_pct", Decimal("3")) or Decimal("3")
        ):
            return StrategyDecision("sell", order_usd, "DIP_EXIT", base_inputs)
        return StrategyDecision("hold", Decimal(0), "DIP_WAIT", base_inputs)

    return StrategyDecision("hold", Decimal(0), "UNKNOWN_KIND", base_inputs)
