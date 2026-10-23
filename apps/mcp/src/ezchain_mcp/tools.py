"""MCP tools: a thin, explicit map onto the ezCHAIN REST API.

There is no live order tool and no client-supplied mode. A write tool is only registered
when the server is started with ``EZCHAIN_MCP_ALLOW_WRITES=1``; the API still requires a
bearer token for every mutation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[EzchainClient, dict[str, Any]], Any]
    read_only: bool = True
    destructive: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
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


def _get_mode(client: EzchainClient, _: dict[str, Any]) -> Any:
    return client.get("/api/v1/agent/capabilities")


def _list_markets(client: EzchainClient, args: dict[str, Any]) -> Any:
    params: dict[str, Any] = {
        key: args[key] for key in ("chain", "q", "sort", "limit") if key in args
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
    return client.post("/api/v1/paper/orders", json=args)


def _run_backtest(client: EzchainClient, args: dict[str, Any]) -> Any:
    return client.post("/api/v1/backtests", json=args)


def _arm_kill_switch(client: EzchainClient, args: dict[str, Any]) -> Any:
    return client.post(
        "/api/v1/system/kill-switch",
        json={"active": True, "reason": args["reason"]},
    )


def _disarm_kill_switch(client: EzchainClient, args: dict[str, Any]) -> Any:
    return client.post(
        "/api/v1/system/kill-switch",
        json={"active": False, "reason": args["reason"]},
    )


def _reload_plugins(client: EzchainClient, _: dict[str, Any]) -> Any:
    return client.post("/api/v1/plugins/reload")


def _validate_plugin_config(client: EzchainClient, args: dict[str, Any]) -> Any:
    return client.post(
        f"/api/v1/plugins/{_seg(args['plugin_id'])}/validate",
        json={"config": args.get("config", {})},
    )


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
        "ezchain_arm_kill_switch",
        "Arm kill switch",
        "Block all strategy-originated execution until disarmed. Reason is required.",
        _object({"reason": {"type": "string"}}, ["reason"]),
        _arm_kill_switch,
        read_only=False,
        destructive=True,
    ),
    Tool(
        "ezchain_disarm_kill_switch",
        "Disarm kill switch",
        "Release the strategy execution block. Reason is required.",
        _object({"reason": {"type": "string"}}, ["reason"]),
        _disarm_kill_switch,
        read_only=False,
    ),
    Tool(
        "ezchain_reload_plugins",
        "Reload plugins",
        "Rescan installed plugin entry points and configured paths.",
        _object({}),
        _reload_plugins,
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


__all__ = ["READ_TOOLS", "WRITE_TOOLS", "Tool", "all_tools", "tool_by_name"]
