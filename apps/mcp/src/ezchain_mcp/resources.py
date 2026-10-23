"""MCP resources: read-only, URI-addressed views of the ezCHAIN API."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote

from .client import ApiError, EzchainClient


class ResourceError(LookupError):
    def __init__(self, uri: str, message: str) -> None:
        super().__init__(message)
        self.uri = uri
        self.message = message


STATIC_RESOURCES: tuple[dict[str, Any], ...] = (
    {
        "uri": "ezchain://state",
        "name": "ezCHAIN state",
        "description": "Mode, kill switch, risk, account, positions, strategies and plugins.",
        "mimeType": "application/json",
    },
    {
        "uri": "ezchain://capabilities",
        "name": "Agent capabilities",
        "description": "Execution mode, auth rules, available tools and MCP configuration.",
        "mimeType": "application/json",
    },
    {
        "uri": "ezchain://markets",
        "name": "Markets",
        "description": "Current multi-chain market rows (50 by default).",
        "mimeType": "application/json",
    },
    {
        "uri": "ezchain://positions",
        "name": "Positions",
        "description": "Open paper positions with server-computed marks and PnL.",
        "mimeType": "application/json",
    },
    {
        "uri": "ezchain://account",
        "name": "Paper account",
        "description": "Cash, equity, realized and unrealized PnL.",
        "mimeType": "application/json",
    },
    {
        "uri": "ezchain://strategies",
        "name": "Strategies",
        "description": "All user strategies, including plugin strategies.",
        "mimeType": "application/json",
    },
    {
        "uri": "ezchain://plugins",
        "name": "Plugins",
        "description": "Installed strategy plugin manifests and discovery issues.",
        "mimeType": "application/json",
    },
    {
        "uri": "ezchain://system",
        "name": "System",
        "description": "Health, readiness and the persisted kill switch.",
        "mimeType": "application/json",
    },
    {
        "uri": "ezchain://ledger",
        "name": "Ledger",
        "description": "Recent append-only journal entries (200 by default).",
        "mimeType": "application/json",
    },
    {
        "uri": "ezchain://reconciliation",
        "name": "Reconciliation",
        "description": "Ledger, cash, fill and position agreement checks.",
        "mimeType": "application/json",
    },
)

RESOURCE_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "uriTemplate": "ezchain://strategies/{strategy_id}",
        "name": "Strategy",
        "description": "One strategy by id.",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "ezchain://strategies/{strategy_id}/runs",
        "name": "Strategy runs",
        "description": "Decision log for one strategy.",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "ezchain://strategies/{strategy_id}/performance",
        "name": "Strategy performance",
        "description": "Paper performance attributed to one strategy.",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "ezchain://strategies/{strategy_id}/live-readiness",
        "name": "Live readiness",
        "description": "Paper-gate evidence for one strategy.",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "ezchain://backtests/{backtest_id}",
        "name": "Backtest",
        "description": "One immutable backtest artifact.",
        "mimeType": "application/json",
    },
)

_STATIC_READERS: dict[str, Callable[[EzchainClient], Any]] = {
    "ezchain://state": lambda client: client.get("/api/v1/agent/state"),
    "ezchain://capabilities": lambda client: client.get("/api/v1/agent/capabilities"),
    "ezchain://markets": lambda client: client.get(
        "/api/v1/markets", params={"chain": "all", "limit": 50}
    ),
    "ezchain://positions": lambda client: client.get("/api/v1/paper/positions"),
    "ezchain://account": lambda client: client.get("/api/v1/paper/account"),
    "ezchain://strategies": lambda client: client.get("/api/v1/strategies"),
    "ezchain://plugins": lambda client: client.get("/api/v1/plugins"),
    "ezchain://system": lambda client: {
        "health": client.get("/healthz"),
        "ready": client.get("/readyz"),
        "kill_switch": client.get("/api/v1/system/kill-switch"),
    },
    "ezchain://ledger": lambda client: client.get("/api/v1/paper/ledger", params={"limit": 200}),
    "ezchain://reconciliation": lambda client: client.get("/api/v1/paper/reconciliation"),
}


def _content(uri: str, value: Any) -> dict[str, Any]:
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(value, separators=(",", ":"), default=str),
            }
        ]
    }


def list_resources(client: EzchainClient) -> list[dict[str, Any]]:
    """Static resources plus concrete URIs for current strategies and backtests."""
    resources = [dict(item) for item in STATIC_RESOURCES]
    try:
        strategies = client.get("/api/v1/strategies")
    except ApiError:
        strategies = []
    if isinstance(strategies, list):
        for strategy in strategies[:50]:
            strategy_id = strategy.get("id") if isinstance(strategy, dict) else None
            if not strategy_id:
                continue
            resources.append(
                {
                    "uri": f"ezchain://strategies/{strategy_id}",
                    "name": f"Strategy {strategy.get('name', strategy_id)}",
                    "description": "One strategy by id.",
                    "mimeType": "application/json",
                }
            )
    try:
        backtests = client.get("/api/v1/backtests")
    except ApiError:
        backtests = []
    if isinstance(backtests, list):
        for backtest in backtests[:20]:
            backtest_id = backtest.get("id") if isinstance(backtest, dict) else None
            if backtest_id:
                resources.append(
                    {
                        "uri": f"ezchain://backtests/{backtest_id}",
                        "name": f"Backtest {backtest_id}",
                        "description": "One immutable backtest artifact.",
                        "mimeType": "application/json",
                    }
                )
    return resources


def read_resource(client: EzchainClient, uri: str) -> dict[str, Any]:
    reader = _STATIC_READERS.get(uri)
    if reader is not None:
        return _content(uri, reader(client))
    if uri.startswith("ezchain://strategies/"):
        rest = uri.removeprefix("ezchain://strategies/")
        parts = [unquote(part) for part in rest.split("/") if part]
        if not parts:
            raise ResourceError(uri, "strategy id is required")
        strategy_id = parts[0]
        suffix = parts[1] if len(parts) > 1 else ""
        path = f"/api/v1/strategies/{strategy_id}"
        if suffix == "runs":
            path += "/runs"
        elif suffix == "performance":
            path += "/performance"
        elif suffix == "live-readiness":
            path += "/live-readiness"
        elif suffix:
            raise ResourceError(uri, f"unknown strategy resource: {suffix}")
        return _content(uri, client.get(path))
    if uri.startswith("ezchain://backtests/"):
        backtest_id = unquote(uri.removeprefix("ezchain://backtests/").strip("/"))
        if not backtest_id:
            raise ResourceError(uri, "backtest id is required")
        return _content(uri, client.get(f"/api/v1/backtests/{backtest_id}"))
    raise ResourceError(uri, "unknown resource")


__all__ = [
    "RESOURCE_TEMPLATES",
    "STATIC_RESOURCES",
    "ResourceError",
    "list_resources",
    "read_resource",
]
