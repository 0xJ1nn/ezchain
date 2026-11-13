"""Paper account and order routes."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from ezchain_adapters import AdapterError, DexScreenerClient, QuoteRouter, get_chain
from ezchain_core import OrderRequest
from ezchain_core.types import RISK_REASON_MESSAGES

from ..actor import ACTOR_UNKNOWN, from_principal
from ..config import Settings, get_settings
from ..deps import get_dexscreener, get_quote_router, get_session
from ..errors import adapter_http_error
from ..models import PaperFillRow, PaperOrderRow
from ..schemas import (
    ActorOut,
    FillOut,
    OrderOut,
    PaperAccountOut,
    PaperOrderIn,
    PaperWalletOut,
    PositionOut,
    ReconciliationOut,
    RiskReasonOut,
)
from ..services import paper, reconciliation

router = APIRouter(prefix="/api/v1/paper", tags=["paper"])


def _dt(value: object) -> str:
    if isinstance(value, datetime):
        return (
            value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
        ).isoformat()
    return str(value)


def _actor(row: PaperOrderRow) -> ActorOut:
    """The persisted actor, read as unknown when the row predates the columns."""
    return ActorOut(kind=row.actor_kind or ACTOR_UNKNOWN, id=row.actor_id)


def _order(row: PaperOrderRow) -> OrderOut:
    return OrderOut(
        id=row.id,
        chain=row.chain,
        token_address=row.token_address,
        side=row.side,
        amount_usd=str(row.amount_usd),
        status=row.status,
        reason=row.reason,
        reasons=[
            RiskReasonOut(
                code=item["code"],
                message=item["message"],
                detail=item.get("detail"),
            )
            for item in (row.reasons or [])
        ]
        if row.reasons is not None
        else (
            [
                RiskReasonOut(
                    code=row.reason,
                    message=RISK_REASON_MESSAGES.get(row.reason, row.reason),
                    detail=None,
                )
            ]
            if row.reason is not None
            else []
        ),
        quote_kind=row.quote_kind,
        actor=_actor(row),
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
def account(session: Session = Depends(get_session)) -> PaperAccountOut:
    """The paper account as a pure ledger projection.

    Read-only by construction: genesis and the wallet declarations are bootstrapped at
    boot and on order placement, so this route never creates a row. A missing wallet is
    reported as missing (its cash appears in the unallocated bucket) instead of being
    healed by the read that is supposed to expose it.
    """
    return PaperAccountOut.model_validate(paper.account_snapshot(session))


@router.get("/wallets", response_model=list[PaperWalletOut])
def wallets(session: Session = Depends(get_session)) -> list[PaperWalletOut]:
    """Every paper wallet with the cash the ledger has settled to it.

    Read-only: the declared wallets are seeded at boot and on order placement, and a
    wallet is a view over the journal whose balance does not exist as a stored number.
    The route never writes, so a read cannot change what the next read sees. Cash whose
    attributed wallet row is gone is reported in a final ``unallocated`` row with a null
    id, so the rows always add up to the account's cash instead of dropping it.
    """
    return [PaperWalletOut.model_validate(row) for row in paper.wallets(session)]


@router.get("/positions", response_model=list[PositionOut])
def positions(session: Session = Depends(get_session)) -> list[PositionOut]:
    return [PositionOut.model_validate(row) for row in paper.positions(session)]


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


@router.get("/reconciliation", response_model=ReconciliationOut)
def reconciliation_report(session: Session = Depends(get_session)) -> ReconciliationOut:
    """Read-only proof that the journal, fills and orders agree."""
    return ReconciliationOut.model_validate(reconciliation.reconciliation(session))


@router.post("/orders", response_model=OrderOut, status_code=201)
async def create_order(
    payload: PaperOrderIn,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    dexscreener: DexScreenerClient = Depends(get_dexscreener),
    quotes: QuoteRouter = Depends(get_quote_router),
) -> OrderOut:
    if get_chain(payload.chain) is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CHAIN_NOT_FOUND", "message": f"unknown chain {payload.chain}"},
        )
    # The actor is the authenticated caller, resolved here and never taken from the body.
    # ``PaperOrderIn`` refuses the field outright, so a client cannot supply one; a request
    # with no principal (the development fallback, or a loopback read-only process) is
    # attributed to ``unknown`` rather than to a guess.
    actor = from_principal(getattr(request.state, "principal", None))
    order_request = OrderRequest(
        chain=payload.chain,
        token_address=payload.token_address,
        side=payload.side,
        amount_usd=payload.amount_usd,
        idempotency_key=payload.idempotency_key,
        quantity=payload.quantity,
        reduce_only=payload.reduce_only,
        risk_profile=payload.risk_profile or payload.trade_risk_profile or payload.profile,
    )
    try:
        row = await paper.place_order(
            session, order_request, settings, dexscreener, quotes, actor=actor
        )
    except paper.IdempotencyConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "IDEMPOTENCY_CONFLICT", "message": str(exc)},
        ) from exc
    except AdapterError as exc:
        raise adapter_http_error(exc) from exc
    if paper.last_created():
        response.status_code = 201
    else:
        response.status_code = 200
    return _order(row)
