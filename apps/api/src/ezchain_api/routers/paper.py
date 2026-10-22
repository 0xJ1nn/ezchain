"""Paper account and order routes."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..deps import get_session
from ..models import PaperFillRow, PaperOrderRow
from ..schemas import FillOut, OrderOut, PaperAccountOut, PaperOrderIn, PositionOut
from ..services import paper

router = APIRouter(prefix="/api/v1/paper", tags=["paper"])


def _dt(value: object) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _order(row: PaperOrderRow) -> OrderOut:
    return OrderOut(
        id=row.id,
        chain=row.chain,
        token_address=row.token_address,
        side=row.side,
        amount_usd=str(row.amount_usd),
        status=row.status,
        reason=row.reason,
        quote_kind=row.quote_kind,
        created_at=_dt(row.created_at),
        filled_at=None if row.filled_at is None else _dt(row.filled_at),
    )


def _fill(row: PaperFillRow) -> FillOut:
    return FillOut(
        id=row.id,
        order_id=row.order_id,
        chain=row.chain,
        token_address=row.token_address,
        side=row.side,
        quantity=str(row.quantity),
        price_usd=str(row.price_usd),
        notional_usd=str(row.notional_usd),
        fee_usd=str(row.fee_usd),
        quote_kind=row.quote_kind,
        created_at=_dt(row.created_at),
    )


@router.get("/account", response_model=PaperAccountOut)
def account(
    session: Session = Depends(get_session), settings: Settings = Depends(get_settings)
) -> PaperAccountOut:
    paper.ensure_genesis(session, settings.paper_starting_cash_usd)
    return PaperAccountOut.model_validate(
        paper.account_snapshot(session, settings.paper_starting_cash_usd)
    )


@router.get("/positions", response_model=list[PositionOut])
def positions(session: Session = Depends(get_session)) -> list[PositionOut]:
    return []


@router.get("/orders", response_model=list[OrderOut])
def order_history(
    limit: int = Query(default=50, ge=1, le=200), session: Session = Depends(get_session)
) -> list[OrderOut]:
    return [_order(row) for row in paper.orders(session, limit)]


@router.get("/fills", response_model=list[FillOut])
def fill_history(
    limit: int = Query(default=50, ge=1, le=200), session: Session = Depends(get_session)
) -> list[FillOut]:
    return [_fill(row) for row in paper.fills(session, limit)]


@router.post("/orders", response_model=OrderOut, status_code=201)
def create_order(payload: PaperOrderIn) -> OrderOut:
    raise HTTPException(
        status_code=501,
        detail={
            "code": "NOT_IMPLEMENTED",
            "message": "paper order execution lands in P0.2",
            "retryable": False,
        },
    )
