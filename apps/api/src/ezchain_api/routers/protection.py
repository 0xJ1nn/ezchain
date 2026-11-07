"""Position protection routes: arm/profile CRUD plus one manual evaluation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ezchain_adapters import DexScreenerClient, get_chain
from ezchain_core.protection import ProtectionValidationError

from ..deps import get_dexscreener, get_session
from ..models import ProtectionArmRow
from ..schemas import (
    ProtectionArmIn,
    ProtectionArmOut,
    ProtectionArmPatch,
    ProtectionEffectiveOut,
    ProtectionEvaluateOut,
    ProtectionProfileIn,
    ProtectionProfileOut,
)
from ..services import protection as protection_service
from ..services.protection import (
    ProtectionServiceError,
    arm_to_dict,
    profile_to_dict,
)

router = APIRouter(prefix="/api/v1/protection", tags=["protection"])


def _get_or_404(session: Session, arm_id: str) -> ProtectionArmRow:
    row = protection_service.get_arm(session, arm_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "ARM_NOT_FOUND", "message": "protection arm not found"},
        )
    return row


def _validation_http(exc: ProtectionValidationError) -> HTTPException:
    status = 404 if exc.code in ("PROFILE_NOT_FOUND",) else 422
    return HTTPException(
        status_code=status,
        detail={"code": exc.code, "message": exc.message, "retryable": False},
    )


@router.get("/arms", response_model=list[ProtectionArmOut])
def list_arms(
    status: str | None = Query(default=None),
    chain: str | None = Query(default=None),
    token_address: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[ProtectionArmOut]:
    return [
        ProtectionArmOut.model_validate(arm_to_dict(row))
        for row in protection_service.list_arms(
            session, status=status, chain=chain, token_address=token_address
        )
    ]


@router.post("/arms", response_model=ProtectionArmOut, status_code=201)
def create_arm(
    payload: ProtectionArmIn, session: Session = Depends(get_session)
) -> ProtectionArmOut:
    if get_chain(payload.chain) is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CHAIN_NOT_FOUND", "message": f"unknown chain {payload.chain}"},
        )
    try:
        row = protection_service.create_arm(
            session,
            chain=payload.chain,
            token_address=payload.token_address,
            token_symbol=payload.token_symbol,
            kind=payload.kind,
            params=payload.params,
            profile_id=payload.profile_id,
            expires_at=payload.expires_at,
        )
    except ProtectionValidationError as exc:
        raise _validation_http(exc) from exc
    return ProtectionArmOut.model_validate(arm_to_dict(row))


@router.get("/arms/{arm_id}", response_model=ProtectionArmOut)
def get_arm(arm_id: str, session: Session = Depends(get_session)) -> ProtectionArmOut:
    return ProtectionArmOut.model_validate(arm_to_dict(_get_or_404(session, arm_id)))


@router.patch("/arms/{arm_id}", response_model=ProtectionArmOut)
def patch_arm(
    arm_id: str, payload: ProtectionArmPatch, session: Session = Depends(get_session)
) -> ProtectionArmOut:
    try:
        row = protection_service.patch_arm(session, arm_id, payload.params)
    except ProtectionValidationError as exc:
        raise _validation_http(exc) from exc
    except ProtectionServiceError as exc:
        status = 404 if exc.code == "ARM_NOT_FOUND" else 409
        raise HTTPException(
            status_code=status,
            detail={"code": exc.code, "message": exc.message, "retryable": False},
        ) from exc
    return ProtectionArmOut.model_validate(arm_to_dict(row))


@router.post("/arms/{arm_id}/cancel", response_model=ProtectionArmOut)
def cancel_arm(arm_id: str, session: Session = Depends(get_session)) -> ProtectionArmOut:
    try:
        row = protection_service.cancel_arm(session, arm_id)
    except ProtectionServiceError as exc:
        status = 404 if exc.code == "ARM_NOT_FOUND" else 409
        raise HTTPException(
            status_code=status,
            detail={"code": exc.code, "message": exc.message, "retryable": False},
        ) from exc
    return ProtectionArmOut.model_validate(arm_to_dict(row))


@router.post("/arms/{arm_id}/evaluate", response_model=ProtectionEvaluateOut)
async def evaluate_arm(
    arm_id: str,
    request: Request,
    session: Session = Depends(get_session),
    dexscreener: DexScreenerClient = Depends(get_dexscreener),
) -> ProtectionEvaluateOut:
    """One engine tick for one arm, through the same OrderExecutor the loop uses."""
    row = _get_or_404(session, arm_id)
    executor = getattr(request.app.state, "order_executor", None)
    if executor is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "ORDER_EXECUTOR_UNAVAILABLE",
                "message": "paper order executor is not wired",
                "retryable": True,
            },
        )
    from ezchain_engine import evaluate_arm_row

    result = await evaluate_arm_row(session, row, dexscreener, executor)
    session.refresh(row)
    return ProtectionEvaluateOut(
        arm=ProtectionArmOut.model_validate(arm_to_dict(row)),
        action=result.action,
        reason=result.reason,
        sell_fraction=str(result.sell_fraction),
        order_id=result.order_id,
    )


@router.get("/profiles", response_model=list[ProtectionProfileOut])
def list_profiles(
    chain: str | None = Query(default=None), session: Session = Depends(get_session)
) -> list[ProtectionProfileOut]:
    return [
        ProtectionProfileOut.model_validate(profile_to_dict(row))
        for row in protection_service.list_profiles(session, chain=chain)
    ]


@router.post("/profiles", response_model=ProtectionProfileOut, status_code=201)
def create_profile(
    payload: ProtectionProfileIn, session: Session = Depends(get_session)
) -> ProtectionProfileOut:
    if payload.chain is not None and get_chain(payload.chain) is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CHAIN_NOT_FOUND", "message": f"unknown chain {payload.chain}"},
        )
    try:
        row = protection_service.create_profile(
            session,
            name=payload.name,
            chain=payload.chain,
            kind=payload.kind,
            params=payload.params,
            rules=payload.rules,
        )
    except ProtectionValidationError as exc:
        raise _validation_http(exc) from exc
    return ProtectionProfileOut.model_validate(profile_to_dict(row))


@router.get("/profiles/effective", response_model=ProtectionEffectiveOut)
def effective_profile(
    chain: str, session: Session = Depends(get_session)
) -> ProtectionEffectiveOut:
    if get_chain(chain) is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CHAIN_NOT_FOUND", "message": f"unknown chain {chain}"},
        )
    row = protection_service.resolve_profile_row(session, chain)
    return ProtectionEffectiveOut(
        chain=chain,
        profile=None if row is None else ProtectionProfileOut.model_validate(profile_to_dict(row)),
    )
