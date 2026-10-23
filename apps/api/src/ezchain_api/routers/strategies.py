"""Strategy CRUD and manual run."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_adapters import DexScreenerClient, get_chain
from ezchain_core.graph import validate as validate_graph
from ezchain_core.strategy import StrategyValidationError
from ezchain_engine import (
    GraphContextBuilder,
    OrderExecutor,
    PluginContextBuilder,
    PluginNotFound,
    PluginRegistry,
)
from ezchain_sdk import ParamsError
from ezchain_sdk import validate_params as validate_plugin_params

from ..config import Settings, get_settings
from ..deps import get_dexscreener, get_plugin_registry, get_session
from ..models import StrategyRow, StrategyRunRow
from ..schemas import (
    StrategyIn,
    StrategyOut,
    StrategyPatch,
    StrategyPerformanceOut,
    StrategyRunOut,
)
from ..services import performance, readiness
from ..services import strategies as strategy_service
from ..services.strategies import (
    MissingOrderExecutor,
    run_strategy_once,
    validate_strategy_params,
)

router = APIRouter(prefix="/api/v1/strategies", tags=["strategies"])


def _dt(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return moment.isoformat()
    return str(value)


def _out(row: StrategyRow) -> StrategyOut:
    return StrategyOut(
        id=row.id,
        name=row.name,
        chain=row.chain,
        token_address=row.token_address,
        token_symbol=row.token_symbol,
        kind=row.kind,
        params=row.params or {},
        plugin_id=row.plugin_id,
        plugin_version=row.plugin_version,
        status=row.status,
        last_run_at=_dt(row.last_run_at),
        created_at=_dt(row.created_at) or "",
    )


def get_or_404(session: Session, strategy_id: str) -> StrategyRow:
    row = session.get(StrategyRow, strategy_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "STRATEGY_NOT_FOUND", "message": "strategy not found"},
        )
    return row


def _validate_params(
    kind: str,
    params: dict[str, object] | None,
    *,
    plugin_id: str | None = None,
    registry: PluginRegistry | None = None,
) -> None:
    try:
        validate_strategy_params(kind, params)
    except StrategyValidationError as exc:
        detail: dict[str, object] = {
            "code": exc.code,
            "message": exc.message,
            "retryable": False,
        }
        if exc.code == "STRATEGY_GRAPH_INVALID":
            # The editor needs the full report to highlight the offending node, exactly
            # as the standalone /graph/validate endpoint returns it.
            detail["report"] = validate_graph((params or {}).get("graph")).to_dict()
        raise HTTPException(status_code=422, detail=detail) from exc

    if kind != "plugin":
        return
    if plugin_id is None or registry is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PLUGIN_NOT_CONFIGURED",
                "message": "plugin_id is required for a plugin strategy",
                "retryable": False,
            },
        )
    try:
        record = registry.get(plugin_id)
    except PluginNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "PLUGIN_NOT_FOUND", "message": f"unknown plugin {plugin_id}"},
        ) from exc
    config = (params or {}).get("config", {})
    if not isinstance(config, Mapping):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "STRATEGY_PARAM_TYPE",
                "message": "config must be an object",
                "retryable": False,
            },
        )
    try:
        validate_plugin_params(record.manifest.params_schema, config)
    except ParamsError as exc:
        raise HTTPException(
            status_code=422,
            detail={**exc.to_dict(), "retryable": False},
        ) from exc


def _order_executor(request: Request) -> OrderExecutor:
    """The composition root sets ``app.state.order_executor``; until then, fail closed."""
    executor = getattr(request.app.state, "order_executor", None)
    return cast(OrderExecutor, executor if executor is not None else MissingOrderExecutor())


def _graph_runtime(request: Request) -> GraphContextBuilder | None:
    """The composition root sets ``app.state.graph_runtime``; absent means no candles."""
    return cast(GraphContextBuilder | None, getattr(request.app.state, "graph_runtime", None))


def _plugin_runtime(request: Request) -> PluginContextBuilder | None:
    return cast(PluginContextBuilder | None, getattr(request.app.state, "plugin_runtime", None))


@router.get("", response_model=list[StrategyOut])
def list_strategies(session: Session = Depends(get_session)) -> list[StrategyOut]:
    rows = session.scalars(select(StrategyRow).order_by(StrategyRow.created_at.desc())).all()
    return [_out(row) for row in rows]


@router.post("", response_model=StrategyOut, status_code=201)
def create_strategy(
    payload: StrategyIn,
    session: Session = Depends(get_session),
    registry: PluginRegistry = Depends(get_plugin_registry),
) -> StrategyOut:
    if get_chain(payload.chain) is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CHAIN_NOT_FOUND", "message": f"unknown chain {payload.chain}"},
        )
    _validate_params(
        payload.kind,
        payload.params,
        plugin_id=payload.plugin_id,
        registry=registry,
    )
    plugin_version = payload.plugin_version
    if payload.kind == "plugin" and payload.plugin_id is not None:
        record = registry.get(payload.plugin_id)
        if record.manifest.chains and payload.chain not in record.manifest.chains:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "PLUGIN_CHAIN_UNSUPPORTED",
                    "message": (
                        f"{payload.plugin_id} does not support {payload.chain}; "
                        f"declared: {', '.join(record.manifest.chains)}"
                    ),
                    "retryable": False,
                },
            )
        if payload.plugin_version not in (None, record.manifest.version):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "PLUGIN_VERSION_MISMATCH",
                    "message": (
                        f"installed {payload.plugin_id} is {record.manifest.version}, "
                        f"not {payload.plugin_version}"
                    ),
                    "retryable": False,
                },
            )
        plugin_version = record.manifest.version
    row = StrategyRow(
        name=payload.name,
        chain=payload.chain,
        token_address=payload.token_address,
        token_symbol=payload.token_symbol,
        kind=payload.kind,
        params=payload.params,
        plugin_id=payload.plugin_id,
        plugin_version=plugin_version,
        status=payload.status,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return _out(row)


@router.get("/{strategy_id}", response_model=StrategyOut)
def get_strategy(strategy_id: str, session: Session = Depends(get_session)) -> StrategyOut:
    return _out(get_or_404(session, strategy_id))


@router.patch("/{strategy_id}", response_model=StrategyOut)
def patch_strategy(
    strategy_id: str,
    payload: StrategyPatch,
    session: Session = Depends(get_session),
    registry: PluginRegistry = Depends(get_plugin_registry),
) -> StrategyOut:
    row = get_or_404(session, strategy_id)
    if payload.name is not None:
        row.name = payload.name
    if payload.params is not None:
        _validate_params(
            row.kind,
            payload.params,
            plugin_id=row.plugin_id,
            registry=registry,
        )
        row.params = payload.params
    if payload.status is not None:
        row.status = payload.status
    session.commit()
    session.refresh(row)
    return _out(row)


@router.delete("/{strategy_id}", status_code=204)
def delete_strategy(strategy_id: str, session: Session = Depends(get_session)) -> None:
    if not strategy_service.delete_strategy(session, strategy_id):
        raise HTTPException(
            status_code=404,
            detail={"code": "STRATEGY_NOT_FOUND", "message": "strategy not found"},
        )


@router.post("/{strategy_id}/run", response_model=StrategyRunOut)
async def run_strategy(
    strategy_id: str,
    request: Request,
    session: Session = Depends(get_session),
    dexscreener: DexScreenerClient = Depends(get_dexscreener),
) -> StrategyRunOut:
    row = get_or_404(session, strategy_id)
    if row.status != "active":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "STRATEGY_PAUSED",
                "message": "paused strategies never run; resume first",
                "retryable": False,
            },
        )
    result = await run_strategy_once(
        session,
        row,
        dexscreener,
        _order_executor(request),
        graph_runtime=_graph_runtime(request),
        plugin_runtime=_plugin_runtime(request),
        plugin_host=getattr(request.app.state, "plugin_host", None),
        plugin_registry=getattr(request.app.state, "plugin_registry", None),
    )
    if result.run_id is None or result.created_at is None:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "STRATEGY_RUN_NOT_LOGGED",
                "message": "the run produced no decision record",
                "retryable": True,
            },
        )
    return StrategyRunOut(
        id=result.run_id,
        strategy_id=result.strategy_id,
        action=result.action,
        reason=result.reason,
        inputs=result.inputs,
        order_id=result.order_id,
        created_at=_dt(result.created_at) or "",
    )


@router.get("/{strategy_id}/performance", response_model=StrategyPerformanceOut)
def strategy_performance(
    strategy_id: str, session: Session = Depends(get_session)
) -> StrategyPerformanceOut:
    """PAPER performance from this strategy's own fills, never the whole account."""
    get_or_404(session, strategy_id)
    body = performance.strategy_performance(session, strategy_id)
    if body is None:  # pragma: no cover - get_or_404 already proved the row exists
        raise HTTPException(
            status_code=404,
            detail={"code": "STRATEGY_NOT_FOUND", "message": "strategy not found"},
        )
    return StrategyPerformanceOut.model_validate(body)


@router.get("/{strategy_id}/live-readiness")
def strategy_live_readiness(
    strategy_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Paper-gate evidence for an owner decision. This endpoint never promotes."""
    row = get_or_404(session, strategy_id)
    return readiness.live_readiness(session, row, settings)


@router.get("/{strategy_id}/runs", response_model=list[StrategyRunOut])
def strategy_runs(
    strategy_id: str, session: Session = Depends(get_session)
) -> list[StrategyRunOut]:
    get_or_404(session, strategy_id)
    rows = session.scalars(
        select(StrategyRunRow)
        .where(StrategyRunRow.strategy_id == strategy_id)
        .order_by(StrategyRunRow.created_at.desc())
    ).all()
    return [
        StrategyRunOut(
            id=row.id,
            strategy_id=row.strategy_id,
            action=row.action,
            reason=row.reason,
            inputs=row.inputs or {},
            order_id=row.order_id,
            created_at=_dt(row.created_at) or "",
        )
        for row in rows
    ]
