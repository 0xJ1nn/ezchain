"""Agent-facing read surface.

A stable, single-call state document for an LLM agent, plus a capability document it can
read before planning. Every mutation still goes through the normal authenticated routes
or the MCP tools; this router never executes anything.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_core import utcnow
from ezchain_engine import PluginRegistry

from ..config import Settings, get_settings
from ..deps import get_plugin_registry, get_session
from ..models import StrategyRow
from ..services import account_read, paper, reconciliation
from ..services.system_flags import read_kill_switch

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])

READ_TOOLS = (
    "get_mode",
    "list_markets",
    "get_token",
    "get_candles",
    "get_account",
    "list_positions",
    "list_orders",
    "list_fills",
    "get_performance",
    "get_ledger",
    "get_reconciliation",
    "list_strategies",
    "get_strategy",
    "get_strategy_runs",
    "get_strategy_performance",
    "get_live_readiness",
    "list_plugins",
    "list_backtests",
    "get_backtest",
    "get_system_status",
)
WRITE_TOOLS = (
    "create_strategy",
    "update_strategy",
    "pause_strategy",
    "resume_strategy",
    "delete_strategy",
    "run_strategy",
    "place_paper_order",
    "run_backtest",
    "arm_kill_switch",
    "disarm_kill_switch",
    "reload_plugins",
)


def _dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    moment = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return moment.isoformat()


@router.get("/capabilities")
def capabilities(
    settings: Settings = Depends(get_settings),
    registry: PluginRegistry = Depends(get_plugin_registry),
) -> dict[str, Any]:
    """What an agent may do on this deployment, before it calls a single tool."""
    return {
        "mode": {
            "execution": settings.execution_mode,
            "data": settings.data_mode,
            "live_available": False,
            "live_blocked_reason": "the live executor is not shipped",
        },
        "auth": {
            "reads": "open",
            "writes": "Authorization: Bearer <EZCHAIN_API_TOKEN>",
            "mcp_write_default": "read-only unless EZCHAIN_MCP_ALLOW_WRITES=1",
        },
        "tools": {"read": list(READ_TOOLS), "write": list(WRITE_TOOLS)},
        "plugins": registry.catalog(),
        "mcp": {
            "transport": "stdio",
            "command": "ezchain-mcp",
            "env": ["EZCHAIN_API_URL", "EZCHAIN_API_TOKEN", "EZCHAIN_MCP_ALLOW_WRITES"],
        },
        "as_of": utcnow().isoformat(),
    }


@router.get("/state")
def state(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    registry: PluginRegistry = Depends(get_plugin_registry),
) -> dict[str, Any]:
    """One bounded snapshot: mode, account, positions, strategies, plugins, risk."""
    account = paper.account_snapshot(session)
    rows = session.scalars(select(StrategyRow).order_by(StrategyRow.created_at.desc())).all()
    report = reconciliation.reconciliation(session)
    return {
        "as_of": utcnow().isoformat(),
        "mode": {
            "execution": settings.execution_mode,
            "data": settings.data_mode,
            "live_available": False,
        },
        "kill_switch": read_kill_switch(session, settings).to_dict(),
        "risk": account_read.limits(session, settings),
        "account": account,
        "strategies": [
            {
                "id": row.id,
                "name": row.name,
                "chain": row.chain,
                "token_address": row.token_address,
                "token_symbol": row.token_symbol,
                "kind": row.kind,
                "plugin_id": row.plugin_id,
                "plugin_version": row.plugin_version,
                "status": row.status,
                "last_run_at": _dt(row.last_run_at),
                "created_at": _dt(row.created_at),
            }
            for row in rows
        ],
        "plugins": registry.catalog(),
        "reconciliation": {
            "ok": report["ok"],
            "totals": report["totals"],
            "failed_checks": [check["id"] for check in report["checks"] if not check["ok"]],
        },
    }
