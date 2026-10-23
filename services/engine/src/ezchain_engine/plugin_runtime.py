"""Plugin-strategy decision seam.

The host owns the context and the order path; this module only validates the intents a
supervised worker returned and maps the first order intent onto the existing
``StrategyDecision`` contract.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_api.models import StrategyRow, StrategyRunRow
from ezchain_core.strategy import PluginParams, StrategyDecision, validate_params
from ezchain_core.types import TokenSnapshot
from ezchain_sdk import Intent, PluginContext

from .plugin_host import PluginHost, PluginTick
from .plugins import PluginNotFound, PluginRecord, PluginRegistry

logger = logging.getLogger(__name__)


@runtime_checkable
class PluginContextBuilder(Protocol):
    """Builds the frozen plugin context from the manifest's declared data scopes."""

    async def build_context(
        self,
        session: Session,
        strategy: StrategyRow,
        *,
        snapshot: TokenSnapshot,
        now: datetime,
    ) -> PluginContext: ...


def _json(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json(item) for item in value]
    return value


def plugin_context_digest(context: PluginContext) -> str:
    payload = context.to_dict()
    # State is an input, but it is also the thing a replay reproduces; include it so two
    # ticks with different state never share a fingerprint.
    encoded = json.dumps(_json(payload), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def latest_plugin_state(session: Session, strategy_id: str) -> dict[str, Any]:
    """The most recent replayable state, read from the run log.

    A restart, a worker crash or a params change must not silently reset a strategy to
    flat. The scan is lazy and normally stops on the immediately previous row.
    """
    statement = (
        select(StrategyRunRow.inputs)
        .where(StrategyRunRow.strategy_id == strategy_id)
        .order_by(StrategyRunRow.created_at.desc(), StrategyRunRow.id.desc())
    )
    for inputs in session.scalars(statement).yield_per(200):
        if isinstance(inputs, dict) and isinstance(inputs.get("plugin_state"), dict):
            return dict(inputs["plugin_state"])
    return {}


def _base_inputs(record: PluginRecord, tick: PluginTick, digest: str) -> dict[str, Any]:
    return {
        "kind": "plugin",
        "plugin_id": record.manifest.id,
        "plugin_version": record.manifest.version,
        "plugin_state": tick.state,
        "intents": list(tick.intents),
        "logs": list(tick.logs),
        "context_digest": digest,
    }


def _intent_decision(
    record: PluginRecord,
    tick: PluginTick,
    digest: str,
    params: PluginParams,
    *,
    position_quantity: Decimal,
    price_usd: Decimal | None,
) -> StrategyDecision:
    inputs = _base_inputs(record, tick, digest)
    for line in tick.logs:
        logger.info(
            "plugin %s %s: %s",
            record.manifest.id,
            line.get("level", "info"),
            line.get("message", ""),
        )
    if not tick.intents:
        return StrategyDecision("hold", Decimal(0), "PLUGIN_NO_ACTION", inputs)
    if "submit_intent" not in record.manifest.permissions:
        inputs["rejected_count"] = len(tick.intents)
        return StrategyDecision("hold", Decimal(0), "PLUGIN_INTENT_NOT_PERMITTED", inputs)
    if len(tick.intents) > 1:
        inputs["rejected_count"] = len(tick.intents)
        return StrategyDecision("hold", Decimal(0), "PLUGIN_MULTIPLE_INTENTS", inputs)
    try:
        intent = Intent.from_dict(tick.intents[0])
    except (TypeError, ValueError) as exc:
        inputs["error"] = str(exc)[:300]
        return StrategyDecision("hold", Decimal(0), "PLUGIN_INTENT_INVALID", inputs)
    inputs["intent"] = intent.to_dict()
    if intent.limit_price is not None or intent.ttl_s is not None or intent.size_pct is not None:
        return StrategyDecision("hold", Decimal(0), "PLUGIN_UNSUPPORTED_INTENT", inputs)
    if intent.notional_usd <= 0:
        return StrategyDecision("hold", Decimal(0), "PLUGIN_INTENT_INVALID", inputs)
    if intent.side == "buy":
        if position_quantity > 0:
            if price_usd is None:
                return StrategyDecision("hold", Decimal(0), "PLUGIN_POSITION_UNKNOWN", inputs)
            position_value = position_quantity * price_usd
        else:
            position_value = Decimal(0)
        headroom = params.max_position_usd - position_value
        # These are strategy parameters, not the workspace risk policy. This clamp is
        # never authorization: the order path resolves the persisted policy and rejects
        # an amount above the effective profile maximum with SIZE_ABOVE_MAX rather than
        # resizing it here.
        allowed = min(intent.notional_usd, params.order_usd, max(Decimal(0), headroom))
        inputs["requested_notional_usd"] = str(intent.notional_usd)
        inputs["clamped_notional_usd"] = str(allowed)
        if allowed <= 0:
            return StrategyDecision("hold", Decimal(0), "MAX_POSITION", inputs)
        return StrategyDecision("buy", allowed, intent.reason[:64], inputs)
    return StrategyDecision(
        intent.side,
        intent.notional_usd,
        intent.reason[:64],
        inputs,
    )


async def evaluate_plugin_strategy(
    session: Session,
    strategy: StrategyRow,
    snapshot: TokenSnapshot,
    runtime: PluginContextBuilder,
    host: PluginHost,
    registry: PluginRegistry,
    *,
    now: datetime,
) -> StrategyDecision:
    plugin_id = strategy.plugin_id
    if not plugin_id:
        return StrategyDecision(
            "hold",
            Decimal(0),
            "PLUGIN_NOT_CONFIGURED",
            {"kind": "plugin", "error": "strategy has no plugin_id"},
        )
    try:
        record = registry.require(plugin_id)
    except PluginNotFound:
        return StrategyDecision(
            "hold",
            Decimal(0),
            "PLUGIN_NOT_FOUND",
            {"kind": "plugin", "plugin_id": plugin_id},
        )
    if record.manifest.chains and strategy.chain not in record.manifest.chains:
        return StrategyDecision(
            "hold",
            Decimal(0),
            "PLUGIN_CHAIN_UNSUPPORTED",
            {
                "kind": "plugin",
                "plugin_id": plugin_id,
                "chain": strategy.chain,
                "allowed": list(record.manifest.chains),
            },
        )

    params = validate_params("plugin", dict(strategy.params or {}))
    if not isinstance(params, PluginParams):  # pragma: no cover - kind is checked upstream
        raise ValueError("not a plugin strategy")
    try:
        context = await runtime.build_context(session, strategy, snapshot=snapshot, now=now)
    except Exception as exc:
        logger.warning(
            "strategy %s plugin context unavailable: %s", strategy.id, type(exc).__name__
        )
        return StrategyDecision(
            "hold",
            Decimal(0),
            "PLUGIN_CONTEXT_UNAVAILABLE",
            {"kind": "plugin", "plugin_id": plugin_id, "error": type(exc).__name__},
        )

    event: dict[str, Any] = {"kind": "timer"}
    if record.manifest.primary_timeframe:
        bars = context.candles_for(record.manifest.primary_timeframe, 1)
        if bars:
            event = {"kind": "candle", "candle": bars[-1].to_dict()}
    # The host knows the ledger position even when the plugin did not request
    # ``read_account``; the per-strategy cap is enforced from the host's copy.
    from .scheduler import position_state

    position = position_state(session, strategy.chain, strategy.token_address)
    price = None if snapshot.stale else snapshot.price_usd
    tick = await host.tick(strategy.id, record, context, event=event)
    digest = plugin_context_digest(context)
    if not tick.ok:
        return StrategyDecision(
            "hold",
            Decimal(0),
            tick.error_code or "PLUGIN_ERROR",
            {
                "kind": "plugin",
                "plugin_id": record.manifest.id,
                "plugin_version": record.manifest.version,
                "error": tick.error_message,
                "context_digest": digest,
            },
        )
    return _intent_decision(
        record,
        tick,
        digest,
        params,
        position_quantity=position.quantity,
        price_usd=price,
    )


__all__ = [
    "PluginContextBuilder",
    "evaluate_plugin_strategy",
    "latest_plugin_state",
    "plugin_context_digest",
]
