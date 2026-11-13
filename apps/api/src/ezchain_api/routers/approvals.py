"""The agent-order approval queue and the per-user automation tier.

An agent order under the ``manual`` tier waits here for a person. These routes are the
decision surface: an agent key may read the queue, and only a person may approve, reject,
or change the tier. Approving executes the stored order through the one paper execution
path; a rejection refuses it with the person's own reason.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ezchain_adapters import AdapterError, DexScreenerClient, QuoteRouter

from ..actor import from_principal
from ..config import Settings, get_settings
from ..deps import get_dexscreener, get_quote_router, get_session
from ..errors import adapter_http_error
from ..schemas import (
    ApprovalDecisionIn,
    ApprovalOut,
    ApprovalRejectIn,
    AutomationOut,
    AutomationTierIn,
)
from ..services import approvals, paper

router = APIRouter(prefix="/api/v1/paper", tags=["approvals"])


def _refusal(exc: approvals.ApprovalError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message, "retryable": False},
    )


@router.get("/approvals", response_model=list[ApprovalOut])
def list_approvals(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> list[ApprovalOut]:
    """The queue, newest first. An agent key may read it; it shows who decided what.

    A ``pending`` row past its window reports ``expired`` without this read writing
    anything, and a stored status that is not one of the declared values reports
    ``failed`` rather than being read as pending.
    """
    try:
        rows = approvals.list_approvals(session, status=status, limit=limit)
    except Exception as exc:  # noqa: BLE001 - an unreadable queue is a typed refusal
        raise _refusal(
            approvals.ApprovalStateUnreadable(f"the approval queue could not be read: {exc}")
        ) from exc
    return [ApprovalOut.model_validate(approvals.to_dict(row)) for row in rows]


@router.get("/approvals/{approval_id}", response_model=ApprovalOut)
def get_approval(approval_id: str, session: Session = Depends(get_session)) -> ApprovalOut:
    row = approvals.get(session, approval_id)
    if row is None:
        raise _refusal(approvals.ApprovalNotFound())
    return ApprovalOut.model_validate(approvals.to_dict(row))


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalOut)
async def approve(
    approval_id: str,
    request: Request,
    payload: ApprovalDecisionIn | None = None,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    dexscreener: DexScreenerClient = Depends(get_dexscreener),
    quotes: QuoteRouter = Depends(get_quote_router),
) -> ApprovalOut:
    """Approve the stored order and execute it, through the normal paper path.

    The body may carry a note; it may not carry an order. What executes is what the agent
    proposed and the server stored, so an approval cannot be widened into a different
    trade. Refusals keep their reason code: an expired approval, an already-decided one,
    and a principal that is not a person each answer with their own code.
    """
    person = from_principal(getattr(request.state, "principal", None))
    note = None if payload is None else payload.note
    try:
        row, _order = await paper.approve_order(
            session,
            approval_id,
            settings,
            dexscreener,
            quotes,
            person=person,
            note=note,
        )
    except approvals.ApprovalError as exc:
        raise _refusal(exc) from exc
    except AdapterError as exc:
        # The service already recorded the spent claim as failed; the refusal is typed.
        raise adapter_http_error(exc) from exc
    return ApprovalOut.model_validate(approvals.to_dict(row))


@router.post("/approvals/{approval_id}/reject", response_model=ApprovalOut)
def reject(
    approval_id: str,
    request: Request,
    payload: ApprovalRejectIn,
    session: Session = Depends(get_session),
) -> ApprovalOut:
    """Refuse the stored order with the person's own reason."""
    person = from_principal(getattr(request.state, "principal", None))
    try:
        row = paper.reject_order(session, approval_id, person=person, note=payload.note)
    except approvals.ApprovalError as exc:
        raise _refusal(exc) from exc
    return ApprovalOut.model_validate(approvals.to_dict(row))


@router.get("/automation", response_model=AutomationOut)
def read_automation(
    session: Session = Depends(get_session), settings: Settings = Depends(get_settings)
) -> AutomationOut:
    """The user's automation tier. A new user is ``manual``: agent orders wait."""
    return AutomationOut.model_validate(approvals.automation_state(session, settings))


@router.put("/automation", response_model=AutomationOut)
def set_automation(
    payload: AutomationTierIn,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AutomationOut:
    """Switch the tier. Only a person may, and ``full_auto`` needs the acknowledgement.

    The acknowledgement is persisted as an append-only consent row naming the user, the
    disclosure version and the moment; returning to ``manual`` leaves that history in
    place. A refused change writes nothing.
    """
    person = from_principal(getattr(request.state, "principal", None))
    try:
        state = approvals.set_tier(
            session,
            tier=payload.tier,
            actor=person,
            settings=settings,
            acknowledge_risk=payload.acknowledge_risk,
        )
    except approvals.ApprovalError as exc:
        raise _refusal(exc) from exc
    return AutomationOut.model_validate(state)
