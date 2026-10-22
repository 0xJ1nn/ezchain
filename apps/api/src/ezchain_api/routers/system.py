"""Health and chain registry."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from ezchain_adapters import CHAINS

from ..config import Settings, get_settings
from ..deps import get_session
from ..schemas import ChainOut, KillSwitchIn, KillSwitchOut
from ..services.system_flags import read_kill_switch, set_kill_switch

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


@router.get("/api/v1/system/kill-switch", response_model=KillSwitchOut)
def get_kill_switch(
    session: Session = Depends(get_session), settings: Settings = Depends(get_settings)
) -> KillSwitchOut:
    """Strategy-scoped operator switch. Persisted in ``system_flags``."""
    return KillSwitchOut.model_validate(read_kill_switch(session, settings).to_dict())


@router.post("/api/v1/system/kill-switch", response_model=KillSwitchOut)
def update_kill_switch(
    payload: KillSwitchIn, session: Session = Depends(get_session)
) -> KillSwitchOut:
    """Arm or release the switch.

    The response carries the persisted state and ``updated_at`` (the audit timestamp).
    There is no authentication yet, so the actor cannot be recorded; ops will add auth
    before this endpoint is exposed outside a trusted network.
    """
    state = set_kill_switch(session, active=payload.active, reason=payload.reason)
    return KillSwitchOut.model_validate(state.to_dict())
