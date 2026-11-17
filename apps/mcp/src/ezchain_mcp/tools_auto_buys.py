"""Auto-buy tools: create, pause and resume recurring paper buys.

An auto-buy is a recurring strategy, so it is created through the strategy route and paused
or resumed through the same status field the platform already enforces. Creating or resuming
one arms buys that fire later, so those tools declare a trade effect; pausing only configures.
"""

from __future__ import annotations

from typing import Any

from .tools import Tool, _object, _only, _seg


def _create(client: Any, args: dict[str, Any]) -> Any:
    body = _only(args, "name", "chain", "token_address", "token_symbol", "params", "status")
    body["kind"] = "dca"
    return client.post("/api/v1/strategies", json=body)


def _set_status(client: Any, args: dict[str, Any], status: str) -> Any:
    return client.patch(
        f"/api/v1/strategies/{_seg(args['strategy_id'])}",
        json={"status": status},
    )


def _pause(client: Any, args: dict[str, Any]) -> Any:
    return _set_status(client, args, "paused")


def _resume(client: Any, args: dict[str, Any]) -> Any:
    return _set_status(client, args, "active")


_CREATE_FIELDS = {
    "name": {"type": "string", "minLength": 1, "maxLength": 120},
    "chain": {"type": "string", "minLength": 2, "maxLength": 40},
    "token_address": {"type": "string", "minLength": 2, "maxLength": 160},
    "token_symbol": {"type": "string", "minLength": 1, "maxLength": 40},
    "params": {"type": "object"},
    "status": {"type": "string", "enum": ["active", "paused"]},
}

TOOLS: tuple[Tool, ...] = (
    Tool(
        "ezchain_create_auto_buy",
        "Create auto-buy",
        "Create a recurring paper buy. It configures automation; it does not place an order now.",
        _object(
            _CREATE_FIELDS,
            ["name", "chain", "token_address", "token_symbol"],
        ),
        _create,
        read_only=False,
        effect="trade",
    ),
    Tool(
        "ezchain_pause_auto_buy",
        "Pause auto-buy",
        "Pause a recurring buy. Paused automation never reaches the executor.",
        _object({"strategy_id": {"type": "string"}}, ["strategy_id"]),
        _pause,
        read_only=False,
    ),
    Tool(
        "ezchain_resume_auto_buy",
        "Resume auto-buy",
        "Resume a paused recurring buy.",
        _object({"strategy_id": {"type": "string"}}, ["strategy_id"]),
        _resume,
        read_only=False,
        effect="trade",
    ),
)

__all__ = ["TOOLS"]
