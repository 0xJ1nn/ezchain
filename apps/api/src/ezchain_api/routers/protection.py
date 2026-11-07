"""Position protection HTTP surface."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_core.protection import ProtectionValidationError

from ..deps import get_session
from ..models import ProtectionProfileRow, ProtectionRow
from ..schemas import (
    ProtectionIn,
    ProtectionOut,
    ProtectionPatch,
    ProtectionProfileIn,
    ProtectionProfileOut,
)
from ..services import protection as protection_service

router = APIRouter(tags=["protection"])


def _dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    moment = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return moment.isoformat()


def _protection_out(row: ProtectionRow) -> ProtectionOut:
    return ProtectionOut(
        id=row.id,
        chain=row.chain,
        token_address=row.token_address,
        kind=row.kind,
        params=row.params or {},
        source=row.source,
        status=row.status,
        version=row.version,
        profile_id=row.profile_id,
        trailing_activated=bool(row.trailing_activated),
        trailing_peak_usd=None if row.trailing_peak is None else str(row.trailing_peak),
        trailing_rung_index=int(row.trailing_rung_index or 0),
        created_at=_dt(row.created_at) or "",
        updated_at=_dt(row.updated_at),
    )


def _profile_out(row: ProtectionProfileRow) -> ProtectionProfileOut:
    return ProtectionProfileOut(
        id=row.id,
        name=row.name,
        profile_type=row.profile_type,
        chain=row.chain,
        defaults=row.defaults or {},
        description=row.description,
        created_at=_dt(row.created_at) or "",
    )


@router.get("/api/v1/protections", response_model=list[ProtectionOut])
def list_protections(
    chain: str | None = None,
    token_address: str | None = None,
    session: Session = Depends(get_session),
) -> list[ProtectionOut]:
    stmt = select(ProtectionRow)
    if chain is not None:
        stmt = stmt.where(ProtectionRow.chain == chain)
    if token_address is not None:
        stmt = stmt.where(ProtectionRow.token_address == token_address)
    rows = session.scalars(stmt.order_by(ProtectionRow.created_at.desc())).all()
    return [_protection_out(row) for row in rows]


@router.post("/api/v1/protections", response_model=ProtectionOut, status_code=201)
def arm_protection(
    payload: ProtectionIn,
    session: Session = Depends(get_session),
) -> ProtectionOut:
    try:
        row = protection_service.arm_protection(
            session,
            chain=payload.chain,
            token_address=payload.token_address,
            kind=payload.kind,
            params=payload.params,
            idempotency_key=payload.idempotency_key,
            profile_id=payload.profile_id,
        )
    except protection_service.ProtectionConflict as exc:
        raise HTTPException(
            status_code=409, detail={"code": "PROTECTION_CONFLICT", "message": str(exc)}
        ) from exc
    except ProtectionValidationError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "PROTECTION_INVALID", "message": str(exc)}
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "PROFILE_NOT_FOUND", "message": str(exc)}
        ) from exc
    return _protection_out(row)


@router.get("/api/v1/protections/{protection_id}", response_model=ProtectionOut)
def get_protection(protection_id: str, session: Session = Depends(get_session)) -> ProtectionOut:
    row = session.get(ProtectionRow, protection_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PROTECTION_NOT_FOUND", "message": "protection not found"},
        )
    return _protection_out(row)


@router.patch("/api/v1/protections/{protection_id}", response_model=ProtectionOut)
def patch_protection(
    protection_id: str,
    payload: ProtectionPatch,
    session: Session = Depends(get_session),
) -> ProtectionOut:
    try:
        row = protection_service.update_protection(
            session,
            protection_id,
            params=payload.params,
            expected_version=payload.expected_version,
            status=payload.status,
            profile_id=payload.profile_id,
        )
    except protection_service.ProtectionConflict as exc:
        raise HTTPException(
            status_code=409, detail={"code": "PROTECTION_CONFLICT", "message": str(exc)}
        ) from exc
    except ProtectionValidationError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "PROTECTION_INVALID", "message": str(exc)}
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "PROTECTION_NOT_FOUND", "message": str(exc)}
        ) from exc
    return _protection_out(row)


@router.delete("/api/v1/protections/{protection_id}", response_model=ProtectionOut)
def delete_protection(protection_id: str, session: Session = Depends(get_session)) -> ProtectionOut:
    try:
        row = protection_service.disarm_protection(session, protection_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "PROTECTION_NOT_FOUND", "message": str(exc)}
        ) from exc
    return _protection_out(row)


@router.get("/api/v1/protection-profiles", response_model=list[ProtectionProfileOut])
def list_profiles(session: Session = Depends(get_session)) -> list[ProtectionProfileOut]:
    rows = session.scalars(
        select(ProtectionProfileRow).order_by(ProtectionProfileRow.created_at.desc())
    ).all()
    return [_profile_out(row) for row in rows]


@router.post("/api/v1/protection-profiles", response_model=ProtectionProfileOut, status_code=201)
def create_profile(
    payload: ProtectionProfileIn,
    session: Session = Depends(get_session),
) -> ProtectionProfileOut:
    row = protection_service.create_profile(
        session,
        name=payload.name,
        profile_type=payload.profile_type,
        defaults=payload.defaults,
        chain=payload.chain,
        description=payload.description,
    )
    return _profile_out(row)
