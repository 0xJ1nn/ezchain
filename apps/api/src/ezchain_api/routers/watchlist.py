"""The persisted watchlist: add, list and remove watched token identities."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..deps import get_session
from ..schemas import WatchlistIn, WatchlistOut
from ..services import watchlist

router = APIRouter(prefix="/api/v1/watchlist", tags=["watchlist"])


def _refusal(exc: watchlist.WatchlistError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": exc.code, "message": exc.message, "retryable": False},
    )


@router.get("", response_model=list[WatchlistOut])
def list_watchlist(session: Session = Depends(get_session)) -> list[WatchlistOut]:
    return [WatchlistOut.model_validate(watchlist.to_dict(row)) for row in watchlist.rows(session)]


@router.post("", response_model=WatchlistOut, status_code=200)
def add_to_watchlist(payload: WatchlistIn, session: Session = Depends(get_session)) -> WatchlistOut:
    """Add once. A repeat returns the same row with the same 200, never a duplicate."""
    try:
        row = watchlist.add(
            session,
            payload.chain,
            payload.base_token,
            symbol=payload.symbol,
            name=payload.name,
        )
    except watchlist.WatchlistError as exc:
        raise _refusal(exc) from exc
    return WatchlistOut.model_validate(watchlist.to_dict(row))


@router.delete("", response_model=dict[str, bool])
def remove_from_watchlist(
    chain: str = Query(min_length=2, max_length=40),
    base_token: str = Query(min_length=2, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, bool]:
    try:
        return {"removed": watchlist.remove(session, chain, base_token)}
    except watchlist.WatchlistError as exc:
        raise _refusal(exc) from exc
