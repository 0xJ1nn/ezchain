"""Agent-facing read surface.

A stable, single-call state document for an LLM agent, plus a capability document it can
read before planning. Every mutation still goes through the normal authenticated routes
or the MCP tools; this router never executes anything.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_core import utcnow
from ezchain_engine import PluginRegistry

from ..config import Settings, get_settings
from ..deps import get_plugin_registry, get_session
from ..models import StrategyRow
from ..services import account_read, api_keys, paper, reconciliation
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


class ApiKeyIn(BaseModel):
    subject: str = Field(min_length=1, max_length=120)
    scopes: list[Literal["read", "trade", "admin"]] = Field(min_length=1)
    label: str | None = Field(default=None, max_length=120)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


@router.get("/capabilities")
def capabilities(
    request: Request,
    settings: Settings = Depends(get_settings),
    registry: PluginRegistry = Depends(get_plugin_registry),
) -> dict[str, Any]:
    """What an agent may do on this deployment, before it calls a single tool."""
    principal = getattr(request.state, "principal", None)
    return {
        "principal": None
        if principal is None
        else {
            "subject": principal.subject,
            "scopes": sorted(principal.scopes),
            "legacy": principal.legacy,
        },
        "mode": {
            "execution": settings.execution_mode,
            "data": settings.data_mode,
            "live_available": False,
            "live_blocked_reason": "the live executor is not shipped",
        },
        "auth": {
            "reads": (
                "Authorization: Bearer <agent-key>" if settings.require_auth_for_reads else "open"
            ),
            "writes": "Authorization: Bearer <agent-key|EZCHAIN_API_TOKEN>",
            "scopes": sorted(api_keys.SCOPES),
            "keys_endpoint": "/api/v1/agent/keys",
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


@router.get("/keys")
def list_agent_keys(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Operator view. Hashes and plaintext are never returned."""
    return [api_keys.public_key(row) for row in api_keys.list_keys(session)]


@router.post("/keys", status_code=201)
def create_agent_key(
    payload: ApiKeyIn,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Mint a scoped key. The plaintext token is shown once and never stored."""
    if settings.environment == "prod" and settings.api_key_pepper is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "API_KEY_PEPPER_REQUIRED",
                "message": "set EZCHAIN_API_KEY_PEPPER before minting keys in production",
                "retryable": False,
            },
        )
    pepper = None if settings.api_key_pepper is None else settings.api_key_pepper.get_secret_value()
    ttl = (
        settings.agent_key_default_ttl_days
        if payload.expires_in_days is None
        else payload.expires_in_days
    )
    try:
        row, token = api_keys.create_key(
            session,
            subject=payload.subject,
            scopes=payload.scopes,
            label=payload.label,
            expires_in_days=ttl,
            pepper=pepper,
        )
    except api_keys.ApiKeyError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": exc.message, "retryable": False},
        ) from exc
    return {
        **api_keys.public_key(row),
        "token": token,
        "warning": "store this token now; it cannot be shown again",
    }


@router.delete("/keys/{key_id}", status_code=204)
def revoke_agent_key(key_id: str, session: Session = Depends(get_session)) -> Response:
    if not api_keys.revoke_key(session, key_id):
        raise HTTPException(
            status_code=404,
            detail={"code": "API_KEY_NOT_FOUND", "message": "unknown or revoked key"},
        )
    return Response(status_code=204)
