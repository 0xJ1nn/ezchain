"""Permission-mode API: read the current mode and limits, or change them as a person."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..actor import ACTOR_HUMAN, PrincipalLike, from_principal
from ..config import Settings, get_settings
from ..deps import get_session
from ..services import agent_permissions

router = APIRouter(prefix="/api/v1/agent", tags=["agent-permissions"])


class ModeIn(BaseModel):
    mode: Literal["ask", "auto", "read_only"]
    user_id: str | None = None


def _refusal(exc: agent_permissions.PermissionError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message, "retryable": False},
    )


def _caller(request: Request) -> tuple[str, PrincipalLike | None]:
    principal = getattr(request.state, "principal", None)
    return (getattr(principal, "subject", "") or ""), principal


def _user(request: Request, settings: Settings, requested: str | None = None) -> str:
    subject, principal = _caller(request)
    actor = from_principal(principal)
    # Only a person may name another owner; an agent always reads and changes its own mode.
    if requested and actor.kind == ACTOR_HUMAN:
        return requested
    return actor.principal or subject or settings.workspace_id


@router.get("/permission-mode")
def read_mode(
    request: Request,
    user_id: str | None = None,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    return agent_permissions.to_dict(session, _user(request, settings, user_id), settings)


@router.put("/permission-mode")
def change_mode(
    payload: ModeIn,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    principal = getattr(request.state, "principal", None)
    try:
        return agent_permissions.set_mode(
            session,
            user=_user(request, settings, payload.user_id),
            mode=payload.mode,
            actor=from_principal(principal),
            settings=settings,
        )
    except agent_permissions.PermissionError as exc:
        raise _refusal(exc) from exc


@router.get("/permission-decisions")
def read_decisions(
    request: Request,
    user_id: str | None = None,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    user = _user(request, settings, user_id)
    return {"decisions": agent_permissions.list_decisions(session, user)}
