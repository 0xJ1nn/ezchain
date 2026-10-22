"""Strategy CRUD. Evaluation lands with P0.3."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_adapters import get_chain

from ..deps import get_session
from ..models import StrategyRow, StrategyRunRow
from ..schemas import StrategyIn, StrategyOut, StrategyPatch, StrategyRunOut

router = APIRouter(prefix="/api/v1/strategies", tags=["strategies"])


def _dt(value: object) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _out(row: StrategyRow) -> StrategyOut:
    return StrategyOut(
        id=row.id,
        name=row.name,
        chain=row.chain,
        token_address=row.token_address,
        token_symbol=row.token_symbol,
        kind=row.kind,
        params=row.params or {},
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


@router.get("", response_model=list[StrategyOut])
def list_strategies(session: Session = Depends(get_session)) -> list[StrategyOut]:
    rows = session.scalars(select(StrategyRow).order_by(StrategyRow.created_at.desc())).all()
    return [_out(row) for row in rows]


@router.post("", response_model=StrategyOut, status_code=201)
def create_strategy(payload: StrategyIn, session: Session = Depends(get_session)) -> StrategyOut:
    if get_chain(payload.chain) is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CHAIN_NOT_FOUND", "message": f"unknown chain {payload.chain}"},
        )
    row = StrategyRow(
        name=payload.name,
        chain=payload.chain,
        token_address=payload.token_address,
        token_symbol=payload.token_symbol,
        kind=payload.kind,
        params=payload.params,
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
    strategy_id: str, payload: StrategyPatch, session: Session = Depends(get_session)
) -> StrategyOut:
    row = get_or_404(session, strategy_id)
    if payload.name is not None:
        row.name = payload.name
    if payload.params is not None:
        row.params = payload.params
    if payload.status is not None:
        row.status = payload.status
    session.commit()
    session.refresh(row)
    return _out(row)


@router.delete("/{strategy_id}", status_code=204)
def delete_strategy(strategy_id: str, session: Session = Depends(get_session)) -> None:
    row = get_or_404(session, strategy_id)
    session.delete(row)
    session.commit()


@router.post("/{strategy_id}/run", response_model=StrategyRunOut)
def run_strategy(strategy_id: str, session: Session = Depends(get_session)) -> StrategyRunOut:
    get_or_404(session, strategy_id)
    raise HTTPException(
        status_code=501,
        detail={
            "code": "NOT_IMPLEMENTED",
            "message": "strategy evaluation lands in P0.3",
            "retryable": False,
        },
    )


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
