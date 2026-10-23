"""Workspace settings: the persisted trade-sizing policy and a read-only preview.

GET is a normal open read like the other server projections. PUT is a mutation and is
covered by the bearer middleware; the principal is recorded on the settings row. The
policy is not written to the ledger.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ezchain_core import TradeSizeLimits, TradeSizeResolution, validate_trade_policy

from ..config import Settings, get_settings
from ..deps import get_session
from ..schemas import (
    RiskProfile,
    TradePolicyIn,
    TradePolicyOut,
    TradeProfilePreviewsOut,
    TradeProfilesModel,
    TradeSizeLimitsModel,
    TradeSizePreviewOut,
)
from ..services import account_read, trade_policy
from ..services.trade_policy import TradePolicyRecord

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


def _dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.isoformat()


def _limits_out(limits: TradeSizeLimits) -> TradeSizeLimitsModel:
    return TradeSizeLimitsModel(
        min_amount_usd=limits.min_amount_usd,
        min_pct_equity=limits.min_pct_equity,
        max_amount_usd=limits.max_amount_usd,
        max_pct_equity=limits.max_pct_equity,
    )


def _optional_limits_out(limits: TradeSizeLimits | None) -> TradeSizeLimitsModel | None:
    return None if limits is None else _limits_out(limits)


def _preview_out(result: TradeSizeResolution, profile: RiskProfile) -> TradeSizePreviewOut:
    return TradeSizePreviewOut(
        profile=profile,
        amount_usd=result.amount_usd,
        reason=result.reason,
        manual=result.manual,
        equity_usd=result.equity_usd,
        limits=_optional_limits_out(result.limits),
    )


def _policy_out(record: TradePolicyRecord, equity_usd: Decimal | None) -> TradePolicyOut:
    policy = record.policy
    previews = trade_policy.previews(policy, equity_usd)
    return TradePolicyOut(
        default_profile=policy.default_profile,  # type: ignore[arg-type]
        min_amount_usd=policy.limits.min_amount_usd,
        min_pct_equity=policy.limits.min_pct_equity,
        max_amount_usd=policy.limits.max_amount_usd,
        max_pct_equity=policy.limits.max_pct_equity,
        profiles=TradeProfilesModel(
            low=_limits_out(policy.profiles["low"]),
            mid=_limits_out(policy.profiles["mid"]),
            high=_limits_out(policy.profiles["high"]),
        ),
        source=record.source,
        updated_at=_dt(record.updated_at),
        updated_by=record.updated_by,
        equity_usd=equity_usd,
        previews=TradeProfilePreviewsOut(
            low=_preview_out(previews["low"], "low"),
            mid=_preview_out(previews["mid"], "mid"),
            high=_preview_out(previews["high"], "high"),
        ),
    )


@router.get("/trade-policy", response_model=TradePolicyOut)
def read_trade_policy(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TradePolicyOut:
    record = trade_policy.load_trade_policy(session, settings)
    return _policy_out(record, account_read.equity_usd(session))


@router.put("/trade-policy", response_model=TradePolicyOut)
def update_trade_policy(
    payload: TradePolicyIn,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TradePolicyOut:
    principal = getattr(request.state, "principal", None)
    updated_by = getattr(principal, "subject", None) or "unauthenticated-dev"
    policy = payload.to_core()
    try:
        record = trade_policy.save_trade_policy(session, policy, updated_by=updated_by)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "TRADE_POLICY_INVALID",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    return _policy_out(record, account_read.equity_usd(session))


@router.get("/trade-policy/preview", response_model=TradeSizePreviewOut)
def preview_trade_size(
    profile: RiskProfile = Query(default="mid"),
    min_amount_usd: Decimal | None = Query(default=None, ge=0),
    min_pct_equity: Decimal | None = Query(default=None, ge=0, le=100),
    max_amount_usd: Decimal | None = Query(default=None, ge=0),
    max_pct_equity: Decimal | None = Query(default=None, ge=0, le=100),
    envelope_min_amount_usd: Decimal | None = Query(default=None, ge=0),
    envelope_min_pct_equity: Decimal | None = Query(default=None, ge=0, le=100),
    envelope_max_amount_usd: Decimal | None = Query(default=None, ge=0),
    envelope_max_pct_equity: Decimal | None = Query(default=None, ge=0, le=100),
    manual_amount_usd: Decimal | None = Query(default=None, gt=0),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TradeSizePreviewOut:
    """Preview a draft policy against current server equity. Nothing is persisted.

    ``profile`` fields are the active tab's unsaved values; ``envelope_*`` are the
    unsaved workspace bounds around it. Omitted values fall back to the persisted policy.
    """
    record = trade_policy.load_trade_policy(session, settings)
    base = record.policy.profiles[profile]
    draft = TradeSizeLimits(
        min_amount_usd=base.min_amount_usd if min_amount_usd is None else min_amount_usd,
        min_pct_equity=base.min_pct_equity if min_pct_equity is None else min_pct_equity,
        max_amount_usd=base.max_amount_usd if max_amount_usd is None else max_amount_usd,
        max_pct_equity=base.max_pct_equity if max_pct_equity is None else max_pct_equity,
    )
    outer = record.policy.limits
    envelope = TradeSizeLimits(
        min_amount_usd=outer.min_amount_usd
        if envelope_min_amount_usd is None
        else envelope_min_amount_usd,
        min_pct_equity=outer.min_pct_equity
        if envelope_min_pct_equity is None
        else envelope_min_pct_equity,
        max_amount_usd=outer.max_amount_usd
        if envelope_max_amount_usd is None
        else envelope_max_amount_usd,
        max_pct_equity=outer.max_pct_equity
        if envelope_max_pct_equity is None
        else envelope_max_pct_equity,
    )
    policy = trade_policy.draft_policy(
        record.policy, profile=profile, limits=draft, envelope=envelope
    )
    errors = validate_trade_policy(policy)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "TRADE_POLICY_INVALID",
                "message": ", ".join(errors),
                "retryable": False,
            },
        )
    result = trade_policy.quantize_resolution(
        trade_policy.resolve_order_size(
            equity_usd=account_read.equity_usd(session),
            record=trade_policy.TradePolicyRecord(policy=policy, source=record.source),
            profile=profile,
            manual_amount_usd=manual_amount_usd,
        )
    )
    return _preview_out(result, profile)
