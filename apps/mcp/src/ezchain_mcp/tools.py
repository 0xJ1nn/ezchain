"""MCP tools: a thin, explicit map onto the ezCHAIN REST API.

There is no live order tool and no client-supplied mode. A write tool is only registered
when the server is started with ``EZCHAIN_MCP_ALLOW_WRITES=1``; the API still requires a
scoped read/trade key for every mutation.

The admin routes — arming and releasing the kill switch, reloading plugins — have no tool
here. An agent never holds an admin credential, so exposing them would promise a call the
API is guaranteed to refuse. The operator performs those over REST.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Any
from urllib.parse import quote

from .client import EzchainClient


def _seg(value: object) -> str:
    return quote(str(value), safe="")


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


class ToolInputError(ValueError):
    """A refused argument, with the stable code the MCP client receives."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# The scopes are the API's agent-key scopes. Reads stay on ``read``; the one mutation needs
# ``trade`` as well. This is a declaration for the client, not a second permission check:
# the server's write gate and the API's key scopes remain the enforcement points.
SCOPE_READ = "read"
SCOPE_TRADE = "trade"

# The runtime's standard trace event is ``TraceEventKind.TOOL_CALL``. The MCP layer cannot
# import the runtime (no new dependency), so it declares the same token as data.
TRACE_EVENT = "tool_call"

# Window -> (candle timeframe, bar count). Enough closed bars for a 14-period indicator,
# including RSI which needs one more close than its period.
WINDOWS: dict[str, tuple[str, int]] = {
    "1h": ("1m", 60),
    "4h": ("5m", 48),
    "1d": ("1h", 24),
    "7d": ("4h", 42),
}
SERIES_KINDS = ("sma", "ema", "rsi")
OVERLAY_KINDS = ("sma", "ema")
INDICATOR_PERIOD = 14
FLAT_EPSILON = 1e-9
ARITHMETIC_NOTE = (
    "Candle prices stay exact decimal strings from the API; floats are used only for the "
    "indicator arithmetic and its points, never for money."
)

_POINT_SCHEMA = _object(
    {"open_time": {"type": "string"}, "value": {"type": "number"}},
    ["open_time", "value"],
)
_SOURCE_SCHEMA = _object(
    {
        "name": {"type": "string"},
        "url": {"type": ["string", "null"]},
        "observed_at": {"type": "string"},
    },
    ["name", "observed_at"],
)
_PROFILE_SCHEMA = _object(
    {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "chain": {"type": ["string", "null"]},
        "kind": {"type": "string"},
        "stop_loss_pct": {"type": ["string", "null"]},
        "trailing_pct": {"type": ["string", "null"]},
        "take_profit_pct": {"type": ["string", "null"]},
        "rules": {"type": "array", "items": {"type": "string"}},
        "created_at": {"type": "string"},
        "updated_at": {"type": "string"},
        "mode": {"type": "string"},
    },
    ["id", "name", "kind", "created_at", "updated_at"],
)

TREND_OUTPUT_SCHEMA = _object(
    {
        "chain": {"type": "string"},
        "token": {"type": "string"},
        "window": {"type": "string", "enum": list(WINDOWS)},
        "series": {"type": "string", "enum": list(SERIES_KINDS)},
        "timeframe": {"type": "string"},
        "period": {"type": "integer"},
        "points": {"type": "array", "items": _POINT_SCHEMA},
        "latest": {"type": ["number", "null"]},
        "direction": {"type": "string", "enum": ["up", "down", "flat"]},
        "source": {"anyOf": [_SOURCE_SCHEMA, {"type": "null"}]},
        "age_seconds": {"type": ["integer", "null"]},
        "stale": {"type": "boolean"},
        "arithmetic": {"type": "string"},
    },
    [
        "chain",
        "token",
        "window",
        "series",
        "timeframe",
        "period",
        "points",
        "latest",
        "direction",
        "age_seconds",
        "stale",
        "arithmetic",
    ],
)
CHART_SPEC_OUTPUT_SCHEMA = _object(
    {
        "spec_version": {"type": "integer"},
        "kind": {"type": "string"},
        "chain": {"type": "string"},
        "token": {"type": "string"},
        "window": {"type": "string", "enum": list(WINDOWS)},
        "timeframe": {"type": "string"},
        "axes": {"type": "object"},
        "series": {"type": "array", "items": {"type": "object"}},
        "overlays": {"type": "array", "items": {"type": "object"}},
        "source": {"anyOf": [_SOURCE_SCHEMA, {"type": "null"}]},
        "age_seconds": {"type": ["integer", "null"]},
        "stale": {"type": "boolean"},
        "arithmetic": {"type": "string"},
    },
    [
        "spec_version",
        "kind",
        "chain",
        "token",
        "window",
        "timeframe",
        "axes",
        "series",
        "overlays",
        "age_seconds",
        "stale",
        "arithmetic",
    ],
)
# The server wraps non-object handler results in a ``result`` field for structured content.
PROFILES_OUTPUT_SCHEMA = _object(
    {"result": {"type": "array", "items": _PROFILE_SCHEMA}}, ["result"]
)
CREATE_PROFILE_OUTPUT_SCHEMA = _object(
    {
        "dry_run": {"type": "boolean"},
        "persisted": {"type": "boolean"},
        "request": {"type": "object"},
        "profile": {"anyOf": [_PROFILE_SCHEMA, {"type": "null"}]},
    },
    ["dry_run", "persisted", "request"],
)


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[EzchainClient, dict[str, Any]], Any]
    read_only: bool = True
    destructive: bool = False
    # Optional contract metadata. Only tools that declare it emit it, so the existing
    # catalogue's `tools/list` response is unchanged.
    scope: str | None = None
    output_schema: dict[str, Any] | None = None
    trace_event: str | None = None

    def to_dict(self) -> dict[str, Any]:
        declared: dict[str, Any] = {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {
                "readOnlyHint": self.read_only,
                "destructiveHint": self.destructive,
                "idempotentHint": self.read_only,
                "openWorldHint": True,
            },
        }
        if self.scope is not None:
            declared["scope"] = self.scope
        if self.output_schema is not None:
            declared["outputSchema"] = self.output_schema
        if self.trace_event is not None:
            declared["traceEvent"] = self.trace_event
        return declared


def _get_mode(client: EzchainClient, _: dict[str, Any]) -> Any:
    return client.get("/api/v1/agent/capabilities")


def _list_markets(client: EzchainClient, args: dict[str, Any]) -> Any:
    params: dict[str, Any] = {
        key: args[key] for key in ("chain", "q", "sort", "limit", "offset", "cursor") if key in args
    }
    return client.get("/api/v1/markets", params=params)


def _get_token(client: EzchainClient, args: dict[str, Any]) -> Any:
    return client.get(f"/api/v1/tokens/{_seg(args['chain'])}/{_seg(args['address'])}")


def _get_candles(client: EzchainClient, args: dict[str, Any]) -> Any:
    params: dict[str, Any] = {key: args[key] for key in ("timeframe", "limit") if key in args}
    return client.get(
        f"/api/v1/tokens/{_seg(args['chain'])}/{_seg(args['address'])}/candles",
        params=params,
    )


def _get_account(client: EzchainClient, _: dict[str, Any]) -> Any:
    return client.get("/api/v1/paper/account")


def _list_positions(client: EzchainClient, _: dict[str, Any]) -> Any:
    return client.get("/api/v1/paper/positions")


def _list_orders(client: EzchainClient, args: dict[str, Any]) -> Any:
    return client.get("/api/v1/paper/orders", params={"limit": args.get("limit", 50)})


def _list_fills(client: EzchainClient, args: dict[str, Any]) -> Any:
    return client.get("/api/v1/paper/fills", params={"limit": args.get("limit", 50)})


def _get_performance(client: EzchainClient, _: dict[str, Any]) -> Any:
    return client.get("/api/v1/paper/performance")


def _get_ledger(client: EzchainClient, args: dict[str, Any]) -> Any:
    params: dict[str, Any] = {key: args[key] for key in ("limit", "account", "kind") if key in args}
    return client.get("/api/v1/paper/ledger", params=params)


def _get_reconciliation(client: EzchainClient, _: dict[str, Any]) -> Any:
    return client.get("/api/v1/paper/reconciliation")


def _list_approvals(client: EzchainClient, args: dict[str, Any]) -> Any:
    params: dict[str, Any] = {key: args[key] for key in ("status", "limit") if key in args}
    return client.get("/api/v1/paper/approvals", params=params)


def _get_automation(client: EzchainClient, _: dict[str, Any]) -> Any:
    return client.get("/api/v1/paper/automation")


def _list_strategies(client: EzchainClient, _: dict[str, Any]) -> Any:
    return client.get("/api/v1/strategies")


def _get_strategy(client: EzchainClient, args: dict[str, Any]) -> Any:
    return client.get(f"/api/v1/strategies/{_seg(args['strategy_id'])}")


def _get_strategy_runs(client: EzchainClient, args: dict[str, Any]) -> Any:
    return client.get(f"/api/v1/strategies/{_seg(args['strategy_id'])}/runs")


def _get_strategy_performance(client: EzchainClient, args: dict[str, Any]) -> Any:
    return client.get(f"/api/v1/strategies/{_seg(args['strategy_id'])}/performance")


def _get_live_readiness(client: EzchainClient, args: dict[str, Any]) -> Any:
    return client.get(f"/api/v1/strategies/{_seg(args['strategy_id'])}/live-readiness")


def _list_plugins(client: EzchainClient, _: dict[str, Any]) -> Any:
    return client.get("/api/v1/plugins")


def _list_backtests(client: EzchainClient, args: dict[str, Any]) -> Any:
    params = {"strategy_id": args["strategy_id"]} if "strategy_id" in args else {}
    return client.get("/api/v1/backtests", params=params)


def _get_backtest(client: EzchainClient, args: dict[str, Any]) -> Any:
    return client.get(f"/api/v1/backtests/{_seg(args['backtest_id'])}")


def _get_system_status(client: EzchainClient, _: dict[str, Any]) -> Any:
    return {
        "health": client.get("/healthz"),
        "ready": client.get("/readyz"),
        "kill_switch": client.get("/api/v1/system/kill-switch"),
        "capabilities": client.get("/api/v1/agent/capabilities"),
    }


def _get_agent_state(client: EzchainClient, _: dict[str, Any]) -> Any:
    return client.get("/api/v1/agent/state")


def _get_graph_catalogue(client: EzchainClient, _: dict[str, Any]) -> Any:
    return client.get("/api/v1/graph/catalogue")


def _validate_graph(client: EzchainClient, args: dict[str, Any]) -> Any:
    return client.post("/api/v1/graph/validate", json={"graph": args.get("graph")})


def _dry_run_graph(client: EzchainClient, args: dict[str, Any]) -> Any:
    return client.post(
        "/api/v1/graph/dry-run",
        json={
            "graph": args.get("graph"),
            "chain": args["chain"],
            "token_address": args["token_address"],
            "max_position_usd": args.get("max_position_usd"),
        },
    )


def _create_strategy(client: EzchainClient, args: dict[str, Any]) -> Any:
    return client.post("/api/v1/strategies", json=args)


def _update_strategy(client: EzchainClient, args: dict[str, Any]) -> Any:
    strategy_id = args.pop("strategy_id")
    return client.patch(f"/api/v1/strategies/{_seg(strategy_id)}", json=args)


def _set_status(client: EzchainClient, strategy_id: str, status: str) -> Any:
    return client.patch(f"/api/v1/strategies/{_seg(strategy_id)}", json={"status": status})


def _pause_strategy(client: EzchainClient, args: dict[str, Any]) -> Any:
    return _set_status(client, str(args["strategy_id"]), "paused")


def _resume_strategy(client: EzchainClient, args: dict[str, Any]) -> Any:
    return _set_status(client, str(args["strategy_id"]), "active")


def _delete_strategy(client: EzchainClient, args: dict[str, Any]) -> Any:
    client.delete(f"/api/v1/strategies/{_seg(args['strategy_id'])}")
    return {"deleted": args["strategy_id"]}


def _run_strategy(client: EzchainClient, args: dict[str, Any]) -> Any:
    return client.post(f"/api/v1/strategies/{_seg(args['strategy_id'])}/run")


def _place_paper_order(client: EzchainClient, args: dict[str, Any]) -> Any:
    """Place a paper order and return the approval it produced, when it waits for one.

    Under the ``manual`` automation tier an agent's order is not filled: the API stores it
    and returns ``status: "pending_approval"`` with the approval's id and status. The
    answer always names both, so an agent cannot mistake "accepted" for "filled"; under
    ``full_auto`` the two fields are null and the order fills like any other.
    """
    order = client.post("/api/v1/paper/orders", json=args)
    if isinstance(order, dict):
        approval_id = order.get("approval_id")
        approval_status = order.get("approval_status")
        return {
            **order,
            "approval_id": approval_id,
            "approval_status": approval_status,
            "executed": order.get("status") == "filled",
            "next_action": (
                "wait_for_a_person"
                if approval_status == "pending"
                else None
                if approval_id is None
                else "none"
            ),
        }
    return order


def _run_backtest(client: EzchainClient, args: dict[str, Any]) -> Any:
    return client.post("/api/v1/backtests", json=args)


def _validate_plugin_config(client: EzchainClient, args: dict[str, Any]) -> Any:
    return client.post(
        f"/api/v1/plugins/{_seg(args['plugin_id'])}/validate",
        json={"config": args.get("config", {})},
    )


# -- market analysis -------------------------------------------------------
#
# Candle closes arrive as exact decimal strings. They are parsed to float *only* inside the
# indicator helpers below: indicators are arithmetic, never money. Nothing here computes a
# price that is later written to the ledger, and no candle value is rounded before display.


def _window(args: dict[str, Any]) -> tuple[str, str, int]:
    value = args.get("window")
    if not isinstance(value, str) or value not in WINDOWS:
        raise ToolInputError(
            "INVALID_WINDOW", f"window must be one of {', '.join(WINDOWS)}, got {value!r}"
        )
    timeframe, limit = WINDOWS[value]
    return value, timeframe, limit


def _candles(
    client: EzchainClient, *, chain: str, token: str, timeframe: str, limit: int
) -> dict[str, Any]:
    data = client.get(
        f"/api/v1/tokens/{_seg(chain)}/{_seg(token)}/candles",
        params={"timeframe": timeframe, "limit": limit},
    )
    if not isinstance(data, dict):
        raise ToolInputError("CANDLES_MALFORMED", "the candle service did not return an object")
    bars = data.get("candles")
    if not isinstance(bars, list) or not bars or data.get("reason") in ("NO_POOL", "CANDLES_EMPTY"):
        raise ToolInputError("UNKNOWN_TOKEN", f"no stored candles for {chain}:{token}")
    return data


def _closes(data: dict[str, Any]) -> tuple[list[float], list[str], list[str]]:
    """(float closes for arithmetic, open times, original decimal close strings)."""
    closes: list[float] = []
    times: list[str] = []
    raw: list[str] = []
    for bar in data["candles"]:
        if not isinstance(bar, dict):
            raise ToolInputError("CANDLES_MALFORMED", "a candle is not an object")
        try:
            value = float(bar["close"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolInputError("CANDLES_MALFORMED", "a candle close is not a number") from exc
        if not isfinite(value):
            raise ToolInputError("CANDLES_MALFORMED", "a candle close is not finite")
        closes.append(value)
        times.append(str(bar.get("open_time", "")))
        raw.append(str(bar["close"]))
    return closes, times, raw


def _indicator(closes: list[float], kind: str) -> list[float]:
    """14-period SMA, EMA or Wilder RSI, aligned to the end of the closes."""
    period = INDICATOR_PERIOD
    if kind == "sma":
        return [sum(closes[i - period : i]) / period for i in range(period, len(closes) + 1)]
    if kind == "ema":
        if len(closes) < period:
            return []
        seed = sum(closes[:period]) / period
        multiplier = 2.0 / (period + 1)
        out = [seed]
        for value in closes[period:]:
            out.append(out[-1] + multiplier * (value - out[-1]))
        return out
    if kind == "rsi":
        if len(closes) <= period:
            return []
        gains: list[float] = []
        losses: list[float] = []
        for index in range(1, len(closes)):
            change = closes[index] - closes[index - 1]
            gains.append(max(change, 0.0))
            losses.append(max(-change, 0.0))

        def level(gain: float, loss: float) -> float:
            if loss == 0:
                return 100.0 if gain > 0 else 50.0
            return 100.0 - 100.0 / (1.0 + gain / loss)

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        out = [level(avg_gain, avg_loss)]
        for index in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[index]) / period
            avg_loss = (avg_loss * (period - 1) + losses[index]) / period
            out.append(level(avg_gain, avg_loss))
        return out
    raise ToolInputError("INVALID_SERIES", f"unsupported series {kind!r}")


def _points(times: list[str], values: list[float]) -> list[dict[str, Any]]:
    offset = len(times) - len(values)
    return [{"open_time": times[offset + i], "value": value} for i, value in enumerate(values)]


def _freshness(data: dict[str, Any]) -> dict[str, Any]:
    """Pass through the API's source and verdict; derive only the age from its clock."""
    source = data.get("source")
    age: int | None = None
    if isinstance(source, dict) and isinstance(source.get("observed_at"), str):
        try:
            observed = datetime.fromisoformat(source["observed_at"].replace("Z", "+00:00"))
        except ValueError:
            observed = None
        if observed is not None:
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=UTC)
            delta = (datetime.now(UTC) - observed).total_seconds()
            # A future-dated source has no usable age; never clamp it to a fresh-looking zero.
            age = None if delta < 0 else int(delta)
    return {"source": source, "age_seconds": age, "stale": bool(data.get("stale", False))}


def _market_trend(client: EzchainClient, args: dict[str, Any]) -> Any:
    window, timeframe, limit = _window(args)
    kind = args.get("series")
    if not isinstance(kind, str) or kind not in SERIES_KINDS:
        raise ToolInputError(
            "INVALID_SERIES", f"series must be one of {', '.join(SERIES_KINDS)}, got {kind!r}"
        )
    chain, token = args["chain"], args["token"]
    data = _candles(client, chain=chain, token=token, timeframe=timeframe, limit=limit)
    closes, times, _raw = _closes(data)
    values = _indicator(closes, kind)
    if len(values) < 2:
        raise ToolInputError(
            "INSUFFICIENT_CANDLES", f"{kind} needs more closed bars for {window} on {chain}:{token}"
        )
    previous, latest = values[-2], values[-1]
    tolerance = FLAT_EPSILON * max(1.0, abs(previous), abs(latest))
    direction = (
        "flat" if abs(latest - previous) <= tolerance else "up" if latest > previous else "down"
    )
    return {
        "chain": chain,
        "token": token,
        "window": window,
        "series": kind,
        "timeframe": timeframe,
        "period": INDICATOR_PERIOD,
        "points": _points(times, values),
        "latest": latest,
        "direction": direction,
        **_freshness(data),
        "arithmetic": ARITHMETIC_NOTE,
    }


def _chart_spec(client: EzchainClient, args: dict[str, Any]) -> Any:
    window, timeframe, limit = _window(args)
    overlays = args.get("overlays", [])
    if not isinstance(overlays, list) or any(
        not isinstance(name, str) or name not in OVERLAY_KINDS for name in overlays
    ):
        raise ToolInputError(
            "INVALID_OVERLAY", f"overlays must be a list of {', '.join(OVERLAY_KINDS)}"
        )
    chain, token = args["chain"], args["token"]
    data = _candles(client, chain=chain, token=token, timeframe=timeframe, limit=limit)
    closes, times, raw = _closes(data)
    return {
        "spec_version": 1,
        "kind": "line_chart",
        "chain": chain,
        "token": token,
        "window": window,
        "timeframe": timeframe,
        "axes": {
            "x": {"field": "open_time", "type": "time"},
            "y": {"field": "value", "type": "price", "scale": "linear", "currency": "USD"},
        },
        "series": [
            {
                "name": "close",
                "type": "line",
                "values": [{"open_time": times[i], "value": close} for i, close in enumerate(raw)],
            }
        ],
        "overlays": [
            {
                "name": name,
                "period": INDICATOR_PERIOD,
                "type": "line",
                "values": _points(times, _indicator(closes, name)),
            }
            for name in dict.fromkeys(overlays)
        ],
        **_freshness(data),
        "arithmetic": ARITHMETIC_NOTE,
    }


# -- protection profiles ---------------------------------------------------


def _list_protection_profiles(client: EzchainClient, _: dict[str, Any]) -> Any:
    return client.get("/api/v1/protection/profiles")


def _percent(args: dict[str, Any], key: str) -> str | None:
    if key not in args or args[key] is None:
        return None
    raw = args[key]
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        raise ToolInputError("INVALID_PERCENTAGE", f"{key} must be a number")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise ToolInputError("INVALID_PERCENTAGE", f"{key} must be a number") from exc
    if not value.is_finite() or value <= 0:
        raise ToolInputError("INVALID_PERCENTAGE", f"{key} must be a finite number above zero")
    return format(value, "f")


def _create_protection_profile(client: EzchainClient, args: dict[str, Any]) -> Any:
    """Create a reusable manual profile through the API; this never arms or places an order.

    Ranges, unknown parameters and the "at least one default" rule stay with the API's
    ``validate_profile``. The tool only parses percentages into exact decimal strings so a
    float cannot leak a binary expansion into a stored protection level. ``dry_run`` is a
    local preview: it validates and returns the request without persisting anything.
    """
    name = args.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ToolInputError("INVALID_NAME", "name must be a non-empty string of at most 120 chars")
    params = {
        key: text
        for key in ("stop_loss_pct", "trailing_pct", "take_profit_pct")
        if (text := _percent(args, key)) is not None
    }
    if not params:
        raise ToolInputError("INVALID_PERCENTAGE", "at least one percentage default is required")
    payload: dict[str, Any] = {
        "name": name.strip(),
        "chain": None,
        "kind": "manual",
        "params": params,
        "rules": [],
    }
    if args.get("dry_run", False):
        return {"dry_run": True, "persisted": False, "request": payload, "profile": None}
    profile = client.post("/api/v1/protection/profiles", json=payload)
    return {"dry_run": False, "persisted": True, "request": payload, "profile": profile}


READ_TOOLS: tuple[Tool, ...] = (
    Tool(
        "ezchain_get_mode",
        "Get execution mode",
        "Execution and data mode, live availability, auth rules and the tool list.",
        _object({}),
        _get_mode,
    ),
    Tool(
        "ezchain_get_agent_state",
        "Get full agent state",
        "One snapshot: mode, kill switch, risk, account, positions, strategies, plugins.",
        _object({}),
        _get_agent_state,
    ),
    Tool(
        "ezchain_list_markets",
        "List markets",
        "Live multi-chain market rows. Use chain='all' for the round-robin overview.",
        _object(
            {
                "chain": {"type": "string", "default": "all"},
                "q": {"type": "string"},
                "sort": {"type": "string", "enum": ["trending", "liquidity", "new"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
            }
        ),
        _list_markets,
    ),
    Tool(
        "ezchain_get_token",
        "Get token snapshot",
        "Current price, liquidity, volume, changes and safety evidence for one token.",
        _object(
            {
                "chain": {"type": "string"},
                "address": {"type": "string"},
            },
            ["chain", "address"],
        ),
        _get_token,
    ),
    Tool(
        "ezchain_get_candles",
        "Get candles",
        "Closed OHLCV bars, oldest first. Unknown volume is null, never zero.",
        _object(
            {
                "chain": {"type": "string"},
                "address": {"type": "string"},
                "timeframe": {
                    "type": "string",
                    "enum": ["1m", "5m", "15m", "1h", "4h", "1d"],
                    "default": "5m",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 300},
            },
            ["chain", "address"],
        ),
        _get_candles,
    ),
    Tool(
        "ezchain_market_trend",
        "Market trend",
        "SMA, EMA or RSI over stored candles for one window. Indicator points are floats; "
        "candle prices stay exact decimal strings. No order is placed.",
        _object(
            {
                "chain": {"type": "string"},
                "token": {"type": "string"},
                "window": {"type": "string", "enum": list(WINDOWS)},
                "series": {"type": "string", "enum": list(SERIES_KINDS)},
            },
            ["chain", "token", "window", "series"],
        ),
        _market_trend,
        scope=SCOPE_READ,
        output_schema=TREND_OUTPUT_SCHEMA,
        trace_event=TRACE_EVENT,
    ),
    Tool(
        "ezchain_chart_spec",
        "Chart spec",
        "Declarative line-chart spec (axes, series, overlays, source and age) for the web UI. "
        "No image generation and no new chart library.",
        _object(
            {
                "chain": {"type": "string"},
                "token": {"type": "string"},
                "window": {"type": "string", "enum": list(WINDOWS)},
                "overlays": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(OVERLAY_KINDS)},
                    "default": [],
                },
            },
            ["chain", "token", "window"],
        ),
        _chart_spec,
        scope=SCOPE_READ,
        output_schema=CHART_SPEC_OUTPUT_SCHEMA,
        trace_event=TRACE_EVENT,
    ),
    Tool(
        "ezchain_list_protection_profiles",
        "List protection profiles",
        "The protection profiles the API already exposes. Read-only.",
        _object({}),
        _list_protection_profiles,
        scope=SCOPE_READ,
        output_schema=PROFILES_OUTPUT_SCHEMA,
        trace_event=TRACE_EVENT,
    ),
    Tool(
        "ezchain_get_account",
        "Get paper account",
        "Cash, equity, realized and unrealized PnL, and open positions. PAPER only.",
        _object({}),
        _get_account,
    ),
    Tool(
        "ezchain_list_positions",
        "List positions",
        "Open paper positions with server-computed marks and PnL.",
        _object({}),
        _list_positions,
    ),
    Tool(
        "ezchain_list_orders",
        "List orders",
        "Recent paper orders, newest first, including rejected orders and their reasons.",
        _object({"limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}}),
        _list_orders,
    ),
    Tool(
        "ezchain_list_fills",
        "List fills",
        "Recent paper fills with quantity, price, notional and fee.",
        _object({"limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}}),
        _list_fills,
    ),
    Tool(
        "ezchain_get_performance",
        "Get paper performance",
        "Account performance and the equity curve, all derived from the ledger.",
        _object({}),
        _get_performance,
    ),
    Tool(
        "ezchain_get_ledger",
        "Get ledger",
        "Append-only journal slice. The ledger is the only accountant.",
        _object(
            {
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 200},
                "account": {"type": "string"},
                "kind": {"type": "string"},
            }
        ),
        _get_ledger,
    ),
    Tool(
        "ezchain_get_reconciliation",
        "Get reconciliation",
        "Proof that ledger groups, cash, fills and positions agree.",
        _object({}),
        _get_reconciliation,
    ),
    Tool(
        "ezchain_list_approvals",
        "List approvals",
        "Agent orders waiting for a person. A pending row is not a fill.",
        _object(
            {
                "status": {
                    "type": "string",
                    "enum": [
                        "pending",
                        "approved",
                        "rejected",
                        "expired",
                        "superseded",
                        "executed",
                        "failed",
                    ],
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            }
        ),
        _list_approvals,
    ),
    Tool(
        "ezchain_get_automation",
        "Get automation tier",
        "Whether this user's agent orders wait for a person or execute automatically.",
        _object({}),
        _get_automation,
    ),
    Tool(
        "ezchain_list_strategies",
        "List strategies",
        "User strategies, including declarative, graph and plugin kinds.",
        _object({}),
        _list_strategies,
    ),
    Tool(
        "ezchain_get_strategy",
        "Get strategy",
        "One strategy, its params, pinned plugin version and status.",
        _object({"strategy_id": {"type": "string"}}, ["strategy_id"]),
        _get_strategy,
    ),
    Tool(
        "ezchain_get_strategy_runs",
        "Get strategy runs",
        "The strategy's append-only decision log, newest first.",
        _object({"strategy_id": {"type": "string"}}, ["strategy_id"]),
        _get_strategy_runs,
    ),
    Tool(
        "ezchain_get_strategy_performance",
        "Get strategy performance",
        "PAPER performance attributed to this strategy's own fills.",
        _object({"strategy_id": {"type": "string"}}, ["strategy_id"]),
        _get_strategy_performance,
    ),
    Tool(
        "ezchain_get_live_readiness",
        "Get live readiness",
        "Paper-gate evidence and checks. Read-only: it never promotes a strategy.",
        _object({"strategy_id": {"type": "string"}}, ["strategy_id"]),
        _get_live_readiness,
    ),
    Tool(
        "ezchain_list_plugins",
        "List plugins",
        "Installed external strategy plugins, manifests, permissions and issues.",
        _object({}),
        _list_plugins,
    ),
    Tool(
        "ezchain_list_backtests",
        "List backtests",
        "Immutable backtest artifacts, optionally filtered by strategy.",
        _object({"strategy_id": {"type": "string"}}),
        _list_backtests,
    ),
    Tool(
        "ezchain_get_backtest",
        "Get backtest",
        "One immutable backtest result.",
        _object({"backtest_id": {"type": "string"}}, ["backtest_id"]),
        _get_backtest,
    ),
    Tool(
        "ezchain_get_system_status",
        "Get system status",
        "Health, readiness, kill switch and capabilities.",
        _object({}),
        _get_system_status,
    ),
    Tool(
        "ezchain_get_graph_catalogue",
        "Get graph catalogue",
        "The node catalogue for the visual strategy graph.",
        _object({}),
        _get_graph_catalogue,
    ),
    Tool(
        "ezchain_validate_graph",
        "Validate graph",
        "Compile a visual graph and return its full validation report.",
        _object({"graph": {"type": "object"}}, ["graph"]),
        _validate_graph,
    ),
    Tool(
        "ezchain_dry_run_graph",
        "Dry-run graph",
        "Evaluate one graph tick against current data. Never places an order.",
        _object(
            {
                "graph": {"type": "object"},
                "chain": {"type": "string"},
                "token_address": {"type": "string"},
                "max_position_usd": {"type": ["number", "string"]},
            },
            ["graph", "chain", "token_address"],
        ),
        _dry_run_graph,
    ),
)

WRITE_TOOLS: tuple[Tool, ...] = (
    Tool(
        "ezchain_create_strategy",
        "Create strategy",
        "Create a declarative, graph or plugin strategy. Plugin params are validated.",
        _object(
            {
                "name": {"type": "string"},
                "chain": {"type": "string"},
                "token_address": {"type": "string"},
                "token_symbol": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["momentum", "dip", "dca", "threshold", "graph", "plugin"],
                },
                "params": {"type": "object"},
                "plugin_id": {"type": "string"},
                "status": {"type": "string", "enum": ["active", "paused"]},
            },
            ["name", "chain", "token_address", "token_symbol", "kind"],
        ),
        _create_strategy,
        read_only=False,
    ),
    Tool(
        "ezchain_create_protection_profile",
        "Create protection profile",
        "Create a reusable manual protection profile through the existing API. Percentages "
        "are sent as exact decimal strings. It never arms protection or places an order; "
        "dry_run previews the request without persisting it.",
        _object(
            {
                "name": {"type": "string"},
                "stop_loss_pct": {"type": ["number", "string"]},
                "trailing_pct": {"type": ["number", "string"]},
                "take_profit_pct": {"type": ["number", "string"]},
                "dry_run": {"type": "boolean", "default": False},
            },
            ["name"],
        ),
        _create_protection_profile,
        read_only=False,
        scope=SCOPE_TRADE,
        output_schema=CREATE_PROFILE_OUTPUT_SCHEMA,
        trace_event=TRACE_EVENT,
    ),
    Tool(
        "ezchain_update_strategy",
        "Update strategy",
        "Rename, retune params, or pause/resume a strategy.",
        _object(
            {
                "strategy_id": {"type": "string"},
                "name": {"type": "string"},
                "params": {"type": "object"},
                "status": {"type": "string", "enum": ["active", "paused"]},
            },
            ["strategy_id"],
        ),
        _update_strategy,
        read_only=False,
    ),
    Tool(
        "ezchain_pause_strategy",
        "Pause strategy",
        "Pause a strategy. Paused strategies never reach the executor.",
        _object({"strategy_id": {"type": "string"}}, ["strategy_id"]),
        _pause_strategy,
        read_only=False,
    ),
    Tool(
        "ezchain_resume_strategy",
        "Resume strategy",
        "Resume a paused strategy.",
        _object({"strategy_id": {"type": "string"}}, ["strategy_id"]),
        _resume_strategy,
        read_only=False,
    ),
    Tool(
        "ezchain_delete_strategy",
        "Delete strategy",
        "Delete a strategy and its decision log. Orders keep the historical id.",
        _object({"strategy_id": {"type": "string"}}, ["strategy_id"]),
        _delete_strategy,
        read_only=False,
        destructive=True,
    ),
    Tool(
        "ezchain_run_strategy",
        "Run strategy now",
        "Evaluate one strategy tick. PAPER only; the kill switch still applies.",
        _object({"strategy_id": {"type": "string"}}, ["strategy_id"]),
        _run_strategy,
        read_only=False,
    ),
    Tool(
        "ezchain_place_paper_order",
        "Place paper order",
        "Place a PAPER order. There is no live order tool; mode is server-derived.",
        _object(
            {
                "chain": {"type": "string"},
                "token_address": {"type": "string"},
                "side": {"type": "string", "enum": ["buy", "sell"]},
                "amount_usd": {"type": ["number", "string"]},
                "idempotency_key": {"type": "string"},
            },
            ["chain", "token_address", "side", "amount_usd", "idempotency_key"],
        ),
        _place_paper_order,
        read_only=False,
    ),
    Tool(
        "ezchain_run_backtest",
        "Run backtest",
        "Run an immutable graph backtest. Declare fees and slippage explicitly.",
        _object(
            {
                "graph": {"type": "object"},
                "strategy_id": {"type": "string"},
                "chain": {"type": "string"},
                "token_address": {"type": "string"},
                "timeframe": {
                    "type": "string",
                    "enum": ["1m", "5m", "15m", "1h", "4h", "1d"],
                },
                "bars": {"type": "integer", "minimum": 1, "maximum": 1000},
                "fee_bps": {"type": "integer", "minimum": 0, "maximum": 10000},
                "slippage_bps": {"type": "integer", "minimum": 0, "maximum": 10000},
                "starting_cash_usd": {"type": ["number", "string"]},
            },
            [
                "timeframe",
                "bars",
                "fee_bps",
                "slippage_bps",
                "starting_cash_usd",
            ],
        ),
        _run_backtest,
        read_only=False,
    ),
    Tool(
        "ezchain_validate_plugin_config",
        "Validate plugin config",
        "Validate and default a plugin configuration without creating a strategy.",
        _object(
            {"plugin_id": {"type": "string"}, "config": {"type": "object"}},
            ["plugin_id"],
        ),
        _validate_plugin_config,
        read_only=False,
    ),
)


def all_tools(allow_writes: bool) -> tuple[Tool, ...]:
    return READ_TOOLS + (WRITE_TOOLS if allow_writes else ())


def tool_by_name(allow_writes: bool) -> dict[str, Tool]:
    return {tool.name: tool for tool in all_tools(allow_writes)}


__all__ = [
    "READ_TOOLS",
    "WRITE_TOOLS",
    "Tool",
    "ToolInputError",
    "all_tools",
    "tool_by_name",
]
