"""The visual strategy graph: catalogue, compile/validate and a live dry run.

The dry run evaluates the same graph the scheduler would, against the same kind of
context, and never touches the order executor. There is no second evaluator here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ezchain_adapters import AdapterError, DexScreenerClient, GeckoTerminalClient, get_chain
from ezchain_core.graph import catalogue_json, evaluate_graph, parse_document, validate
from ezchain_core.types import utcnow

from ..config import Settings, get_settings
from ..deps import get_dexscreener, get_geckoterminal, get_session
from ..errors import candles_http_error
from ..schemas import GraphDryRunIn, GraphValidateIn
from ..services import graph as graph_service

router = APIRouter(prefix="/api/v1/graph", tags=["graph"])


def _invalid(message: str, report: dict[str, Any] | None = None) -> HTTPException:
    detail: dict[str, Any] = {
        "code": "STRATEGY_GRAPH_INVALID",
        "message": message,
        "retryable": False,
    }
    if report is not None:
        detail["report"] = report
    return HTTPException(status_code=422, detail=detail)


def _document(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid("graph must be a JSON object")
    return value


@router.get("/catalogue")
def get_catalogue() -> dict[str, Any]:
    """The one node table the editor renders. Read-only and deterministic."""
    return catalogue_json()


@router.post("/validate")
def validate_graph(payload: GraphValidateIn) -> dict[str, Any]:
    """Compile a document.

    A well-formed JSON object always returns 200 with the full report — an invalid graph
    is a normal, editable state for the builder. Only a missing/non-object ``graph`` is a
    422, because there is nothing to validate.
    """
    graph = _document(payload.graph)
    return validate(graph).to_dict()


@router.post("/dry-run")
async def dry_run(
    payload: GraphDryRunIn,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    dexscreener: DexScreenerClient = Depends(get_dexscreener),
    geckoterminal: GeckoTerminalClient = Depends(get_geckoterminal),
) -> dict[str, Any]:
    """Evaluate one tick against live data. Never places an order."""
    graph = _document(payload.graph)
    report = validate(graph)
    if not report.ok:
        raise _invalid("the graph does not compile", report.to_dict())
    if get_chain(payload.chain) is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CHAIN_NOT_FOUND", "message": f"unknown chain {payload.chain}"},
        )
    document = parse_document(graph)
    now = utcnow()
    try:
        snapshot = await dexscreener.get_token(payload.chain, payload.token_address)
        tick = await graph_service.build_tick(
            session=session,
            candle_source=geckoterminal,
            snapshot_source=dexscreener,
            document=document,
            chain=payload.chain,
            token_address=payload.token_address,
            snapshot=snapshot,
            now=now,
            max_position_usd=payload.max_position_usd,
            data_mode=settings.data_mode,
        )
    except AdapterError as exc:
        raise candles_http_error(exc) from exc

    decision = evaluate_graph(document, tick.context)
    body = decision.to_dict()
    trace = body.pop("trace", {"values": {}, "exec": [], "unknown": []})
    price = None if snapshot.stale else snapshot.price_usd
    quantity = tick.context.position_quantity
    return {
        "decision": body,
        "trace": trace,
        "context": {
            "now": now.isoformat(),
            "price_usd": None if price is None else str(price),
            "candles": {
                timeframe: {
                    "bars": summary.bars,
                    "last_close_time": None
                    if summary.last_close_time is None
                    else summary.last_close_time.isoformat(),
                    "stale": summary.stale,
                }
                for timeframe, summary in tick.candles.items()
            },
            "position": {
                "open": quantity > 0,
                "quantity": str(quantity),
                "avg_cost_usd": None
                if tick.context.position_avg_cost_usd is None
                else str(tick.context.position_avg_cost_usd),
                "value_usd": None
                if tick.context.position_value_usd is None
                else str(tick.context.position_value_usd),
            },
            "account": {
                "cash_usd": None if tick.context.cash_usd is None else str(tick.context.cash_usd),
                "equity_usd": None
                if tick.context.equity_usd is None
                else str(tick.context.equity_usd),
                "daily_realized_pnl_usd": None
                if tick.context.daily_realized_pnl_usd is None
                else str(tick.context.daily_realized_pnl_usd),
                "open_positions": tick.context.open_positions,
            },
        },
        "mode": "DRY_RUN",
    }


__all__ = ["router"]
