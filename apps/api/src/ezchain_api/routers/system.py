"""Health and chain registry."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from ezchain_adapters import CHAINS

from ..config import Settings, get_settings
from ..deps import get_session
from ..schemas import ChainOut

router = APIRouter(tags=["system"])


@router.get("/health")
def health(
    session: Session = Depends(get_session), settings: Settings = Depends(get_settings)
) -> dict[str, object]:
    try:
        session.execute(text("SELECT 1"))
        db = "ok"
    except Exception:
        db = "down"
    return {
        "status": "ok" if db == "ok" else "degraded",
        "db": db,
        "mode": settings.data_mode,
        "engine_inline": settings.engine_inline,
        "version": "0.1.0",
    }


@router.get("/api/v1/chains", response_model=list[ChainOut])
def chains() -> list[ChainOut]:
    return [ChainOut.model_validate(chain.to_dict()) for chain in CHAINS]
