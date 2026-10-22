"""Graph-strategy runtime seam for the engine.

The evaluator lives in :mod:`ezchain_core.graph`; this module only turns its decision
into the engine's existing ``StrategyDecision`` shape, computes the context fingerprint
and persists/reads the variables carried by the run log. It never places an order.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_api.models import StrategyRow, StrategyRunRow
from ezchain_core.graph import EvalContext, GraphDecision, evaluate_graph
from ezchain_core.strategy import GraphParams, StrategyDecision, validate_params
from ezchain_core.types import TokenSnapshot


@runtime_checkable
class GraphContextBuilder(Protocol):
    """Builds the frozen graph context (snapshot, candles, position, account, vars).

    The engine owns the decision and the order path; the composition root owns candle
    and ledger access, so this port keeps ``services/engine`` free of HTTP and provider
    clients. A missing builder fails closed: a graph never runs with invented inputs.
    """

    async def build_context(
        self,
        session: Session,
        strategy: StrategyRow,
        *,
        snapshot: TokenSnapshot,
        now: datetime,
    ) -> EvalContext: ...


def _json_value(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    return value


def graph_context_digest(ctx: EvalContext) -> str:
    """A stable fingerprint of the exact inputs one graph tick saw.

    Stored in the run log so a recorded decision names the context it was made from
    (CONTRACT I9). It is not a substitute for the context builder.
    """
    payload = {
        "now": ctx.now.isoformat(),
        "snapshot": {
            "price_usd": ctx.snapshot.price_usd,
            "liquidity_usd": ctx.snapshot.liquidity_usd,
            "volume_24h_usd": ctx.snapshot.volume_24h_usd,
            "price_change_m5": ctx.snapshot.price_change_m5,
            "price_change_h1": ctx.snapshot.price_change_h1,
            "price_change_h6": ctx.snapshot.price_change_h6,
            "price_change_h24": ctx.snapshot.price_change_h24,
            "stale": ctx.snapshot.stale,
        },
        "candles": {
            timeframe: [
                {
                    "open_time": bar.open_time.isoformat(),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                }
                for bar in bars
            ]
            for timeframe, bars in ctx.candles.items()
        },
        "position_quantity": ctx.position_quantity,
        "position_avg_cost_usd": ctx.position_avg_cost_usd,
        "position_value_usd": ctx.position_value_usd,
        "cash_usd": ctx.cash_usd,
        "equity_usd": ctx.equity_usd,
        "daily_realized_pnl_usd": ctx.daily_realized_pnl_usd,
        "open_positions": ctx.open_positions,
        "last_fill_at": None if ctx.last_fill_at is None else ctx.last_fill_at.isoformat(),
        "entry_at": None if ctx.entry_at is None else ctx.entry_at.isoformat(),
        "peak_since_entry_usd": ctx.peak_since_entry_usd,
        "vars": ctx.vars,
    }
    encoded = json.dumps(_json_value(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _parse_graph_vars(raw: object) -> dict[str, Decimal | None]:
    if not isinstance(raw, dict):
        return {}
    values: dict[str, Decimal | None] = {}
    for name, value in raw.items():
        if not isinstance(name, str):
            continue
        if value is None:
            values[name] = None
        elif isinstance(value, bool) or not isinstance(value, int | float | str):
            continue
        else:
            try:
                values[name] = Decimal(str(value))
            except (ArithmeticError, ValueError):
                continue
    return values


def latest_graph_vars(session: Session, strategy_id: str) -> dict[str, Decimal | None]:
    """The variables produced by the strategy's most recent run that recorded them.

    A graph run writes its resulting variables into ``inputs.graph_vars``. Holds that
    happen before evaluation (interval, missing market data, no runtime) copy the last
    known values forward, so a run never silently forgets them. The scan is lazy and
    normally stops on the immediately previous row.
    """
    statement = (
        select(StrategyRunRow.inputs)
        .where(StrategyRunRow.strategy_id == strategy_id)
        .order_by(StrategyRunRow.created_at.desc(), StrategyRunRow.id.desc())
    )
    for inputs in session.scalars(statement).yield_per(200):
        if isinstance(inputs, dict) and "graph_vars" in inputs:
            return _parse_graph_vars(inputs.get("graph_vars"))
    return {}


def serialize_graph_vars(values: Mapping[str, Decimal | None]) -> dict[str, str | None]:
    return {name: None if value is None else str(value) for name, value in values.items()}


def _graph_vars(ctx: EvalContext, decision: GraphDecision) -> dict[str, Decimal | None]:
    """Apply ``set_var`` effects after the tick is recorded (the caller persists them)."""
    values = dict(ctx.vars)
    for effect in decision.effects:
        if effect.get("kind") != "set_var":
            continue
        name = effect.get("name")
        value = effect.get("value")
        if isinstance(name, str) and isinstance(value, Decimal):
            values[name] = value
    return values


def _graph_inputs(
    ctx: EvalContext, decision: GraphDecision, variables: Mapping[str, Decimal | None]
) -> dict[str, Any]:
    body = decision.to_dict()
    return {
        "kind": "graph",
        "graph_trace": body["trace"],
        "effects": body["effects"],
        "context_digest": graph_context_digest(ctx),
        "graph_vars": serialize_graph_vars(variables),
    }


def _graph_decision(ctx: EvalContext, decision: GraphDecision) -> StrategyDecision:
    """Map one graph decision onto the existing buy/sell/hold contract.

    A buy carries the evaluator's USD amount. A sell carries exactly
    ``sell_fraction * current position value`` and is capped at that value, so a graph
    can never ask the paper service to sell more than the position (no oversell).
    """
    variables = _graph_vars(ctx, decision)
    inputs = _graph_inputs(ctx, decision, variables)
    if decision.action == "buy":
        amount = decision.amount_usd
        if not isinstance(amount, Decimal) or amount <= 0:
            return StrategyDecision("hold", Decimal(0), "GRAPH_NO_ACTION", inputs)
        return StrategyDecision("buy", amount, decision.reason, inputs)

    if decision.action == "sell":
        fraction = decision.sell_fraction
        value = ctx.position_value_usd
        if value is None:
            price = None if ctx.snapshot.stale else ctx.snapshot.price_usd
            value = None if price is None else ctx.position_quantity * price
        if not isinstance(fraction, Decimal) or fraction <= 0 or value is None:
            return StrategyDecision("hold", Decimal(0), "GRAPH_NO_ACTION", inputs)
        amount = min(value * min(fraction, Decimal(1)), value)
        if amount <= 0:
            return StrategyDecision("hold", Decimal(0), "NO_POSITION", inputs)
        return StrategyDecision("sell", amount, decision.reason, inputs)

    return StrategyDecision("hold", Decimal(0), decision.reason, inputs)


async def evaluate_graph_strategy(
    session: Session,
    strategy: StrategyRow,
    snapshot: TokenSnapshot,
    graph_runtime: GraphContextBuilder,
    *,
    now: datetime,
) -> StrategyDecision:
    params = validate_params("graph", dict(strategy.params or {}))
    if not isinstance(params, GraphParams):  # pragma: no cover - kind is checked upstream
        raise ValueError("not a graph strategy")
    context = await graph_runtime.build_context(session, strategy, snapshot=snapshot, now=now)
    decision = evaluate_graph(params.document, context)
    return _graph_decision(context, decision)


__all__ = [
    "GraphContextBuilder",
    "evaluate_graph_strategy",
    "graph_context_digest",
    "latest_graph_vars",
    "serialize_graph_vars",
]
