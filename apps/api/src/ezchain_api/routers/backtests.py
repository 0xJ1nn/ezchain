"""Immutable graph backtests: create, list and read."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ezchain_adapters import AdapterError, DexScreenerClient, GeckoTerminalClient, get_chain
from ezchain_core.strategy import GraphParams, StrategyValidationError, validate_params
from ezchain_core.types import utcnow

from ..config import Settings, get_settings
from ..deps import get_dexscreener, get_geckoterminal, get_session
from ..errors import candles_http_error
from ..models import StrategyRow
from ..schemas import BacktestIn, BacktestOut
from ..services import backtests as service

router = APIRouter(prefix="/api/v1/backtests", tags=["backtests"])


def _graph_from_request(
    payload: BacktestIn, session: Session
) -> tuple[object, StrategyRow | None, str, str, str, Decimal | None]:
    row: StrategyRow | None = None
    if payload.strategy_id is not None:
        row = session.get(StrategyRow, payload.strategy_id)
        if row is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "STRATEGY_NOT_FOUND", "message": "strategy not found"},
            )
        if row.kind != "graph":
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "BACKTEST_STRATEGY_NOT_GRAPH",
                    "message": "only a graph strategy can be backtested",
                    "retryable": False,
                },
            )
        if payload.chain is not None and payload.chain != row.chain:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "BACKTEST_STRATEGY_MISMATCH",
                    "message": "chain does not match the strategy",
                    "retryable": False,
                },
            )
        if payload.token_address is not None and payload.token_address != row.token_address:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "BACKTEST_STRATEGY_MISMATCH",
                    "message": "token_address does not match the strategy",
                    "retryable": False,
                },
            )
        graph = (row.params or {}).get("graph")
        chain = row.chain
        token_address = row.token_address
        symbol = row.token_symbol
    else:
        if payload.chain is None or payload.token_address is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "BACKTEST_TARGET_REQUIRED",
                    "message": "chain and token_address are required with a graph",
                    "retryable": False,
                },
            )
        graph = payload.graph
        chain = payload.chain
        token_address = payload.token_address
        symbol = token_address

    if get_chain(chain) is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CHAIN_NOT_FOUND", "message": f"unknown chain {chain}"},
        )
    max_position: Decimal | None = None
    if row is not None:
        try:
            params = validate_params("graph", dict(row.params or {}))
        except StrategyValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": False,
                },
            ) from exc
        if isinstance(params, GraphParams):
            max_position = params.max_position_usd
    return graph, row, chain, token_address, symbol, max_position


@router.post("", response_model=BacktestOut, status_code=201)
async def create_backtest(
    payload: BacktestIn,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    dexscreener: DexScreenerClient = Depends(get_dexscreener),
    geckoterminal: GeckoTerminalClient = Depends(get_geckoterminal),
) -> dict[str, Any]:
    graph, row, chain, token_address, symbol, max_position = _graph_from_request(payload, session)
    if graph is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "STRATEGY_GRAPH_INVALID",
                "message": "the strategy has no params.graph document",
                "retryable": False,
            },
        )
    try:
        body = await service.create_backtest(
            session=session,
            graph=graph,
            strategy_id=None if row is None else row.id,
            chain=chain,
            token_address=token_address,
            timeframe=payload.timeframe,
            bars=payload.bars,
            fee_bps=payload.fee_bps,
            slippage_bps=payload.slippage_bps,
            starting_cash_usd=payload.starting_cash_usd,
            max_position_usd=max_position,
            symbol=symbol,
            candle_source=geckoterminal,
            snapshot_source=dexscreener,
            data_mode=settings.data_mode,
            now=utcnow(),
        )
    except service.BacktestRequestError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"code": exc.code, "message": exc.message, "retryable": False},
        ) from exc
    except AdapterError as exc:
        raise candles_http_error(exc) from exc
    return body


@router.get("", response_model=list[dict[str, Any]])
def list_backtests(
    strategy_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    return service.list_backtests(session, strategy_id=strategy_id)


@router.get("/{backtest_id}", response_model=BacktestOut)
def get_backtest(backtest_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    body = service.get_backtest(session, backtest_id)
    if body is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "BACKTEST_NOT_FOUND", "message": "backtest not found"},
        )
    return body
