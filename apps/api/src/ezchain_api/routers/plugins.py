"""Installed strategy plugin catalogue and parameter validation.

Installation is an operator action: ``pip install`` into the API/engine environment or a
directory under ``EZCHAIN_PLUGIN_PATHS``. This router never downloads or executes a
package; ``reload`` only rescans what is already on disk.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_engine import PluginHost, PluginNotFound, PluginRegistry
from ezchain_sdk import ParamsError, validate_params

from ..deps import get_plugin_host, get_plugin_registry, get_session
from ..models import StrategyRow

router = APIRouter(prefix="/api/v1/plugins", tags=["plugins"])


class PluginParamsIn(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)


def _not_found(plugin_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "PLUGIN_NOT_FOUND", "message": f"unknown plugin {plugin_id}"},
    )


@router.get("")
def list_plugins(
    registry: PluginRegistry = Depends(get_plugin_registry),
) -> dict[str, Any]:
    return registry.catalog()


@router.post("/reload")
async def reload_plugins(
    registry: PluginRegistry = Depends(get_plugin_registry),
    host: PluginHost = Depends(get_plugin_host),
) -> dict[str, Any]:
    """Rescan installed entry points and configured paths, then retire live workers."""
    registry.reload()
    await host.close_all()
    return registry.catalog()


@router.get("/{plugin_id}")
def get_plugin(
    plugin_id: str, registry: PluginRegistry = Depends(get_plugin_registry)
) -> dict[str, Any]:
    try:
        return registry.get(plugin_id).to_dict()
    except PluginNotFound as exc:
        raise _not_found(plugin_id) from exc


@router.post("/{plugin_id}/validate")
def validate_plugin_config(
    plugin_id: str,
    payload: PluginParamsIn,
    registry: PluginRegistry = Depends(get_plugin_registry),
) -> dict[str, Any]:
    """Validate and default a configuration without creating a strategy."""
    try:
        record = registry.get(plugin_id)
    except PluginNotFound as exc:
        raise _not_found(plugin_id) from exc
    try:
        config = validate_params(record.manifest.params_schema, payload.config)
    except ParamsError as exc:
        raise HTTPException(
            status_code=422,
            detail={**exc.to_dict(), "retryable": False},
        ) from exc
    return {"plugin_id": plugin_id, "config": config, "manifest_sha256": record.manifest_sha256}


@router.get("/{plugin_id}/health")
def plugin_health(
    plugin_id: str,
    registry: PluginRegistry = Depends(get_plugin_registry),
    host: PluginHost = Depends(get_plugin_host),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        record = registry.get(plugin_id)
    except PluginNotFound as exc:
        raise _not_found(plugin_id) from exc
    quarantined = host.quarantined()
    strategy_ids = set(
        session.scalars(select(StrategyRow.id).where(StrategyRow.plugin_id == plugin_id)).all()
    )
    return {
        "plugin_id": plugin_id,
        "status": "available",
        "manifest_sha256": record.manifest_sha256,
        "quarantined_strategies": {
            strategy_id: reason
            for strategy_id, reason in quarantined.items()
            if strategy_id in strategy_ids
        },
    }
