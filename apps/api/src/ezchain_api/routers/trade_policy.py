"""Trade policy: the profiles and sizing fields the order gate applies.

The read model is the same policy the order gate enforces, including each strategy's
effective profile and any sizing the profile would refuse. The write is a per-strategy
profile override; the middleware already refuses a non-read without a principal, so a
browser cannot change a fund-affecting limit anonymously. The preview runs the order
gate's own sizing resolver, so the verdict on the page is the verdict the order gets.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ezchain_core.trade_policy import PolicyError

from ..config import Settings, get_settings
from ..deps import get_session
from ..schemas_trade_policy import (
    StrategyPolicyOut,
    StrategyRiskPolicyIn,
    TradePolicyOut,
    TradePolicyPreviewOut,
)
from ..services import trade_policy

router = APIRouter(prefix="/api/v1/trade-policy", tags=["trade-policy"])


def _refusal(exc: PolicyError, status_code: int = 422) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": exc.message, "retryable": False},
    )


@router.get("", response_model=TradePolicyOut)
def read_trade_policy(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TradePolicyOut:
    """Every profile, its fields, and each strategy's effective policy."""
    return TradePolicyOut.model_validate(trade_policy.describe(session, settings))


@router.get("/preview", response_model=TradePolicyPreviewOut)
def preview_size(
    amount_usd: Decimal = Query(gt=0),
    equity_usd: Decimal | None = Query(default=None, ge=0),
    profile: str | None = Query(default=None),
    settings: Settings = Depends(get_settings),
) -> TradePolicyPreviewOut:
    """Check one proposed buy against a profile with the order gate's resolver."""
    requested = profile if profile is not None else settings.trade_risk_profile
    try:
        return TradePolicyPreviewOut.model_validate(
            trade_policy.preview(requested, amount_usd, equity_usd)
        )
    except PolicyError as exc:
        raise _refusal(exc) from exc


@router.put("/strategies/{strategy_id}", response_model=StrategyPolicyOut)
def update_strategy_policy(
    strategy_id: str,
    payload: StrategyRiskPolicyIn,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> StrategyPolicyOut:
    """Set or clear one strategy's profile, with acknowledgement at a loosening."""
    principal = getattr(request.state, "principal", None)
    actor = getattr(principal, "subject", None)
    try:
        updated = trade_policy.update_strategy(
            session,
            settings,
            strategy_id,
            risk_profile=payload.risk_profile,
            acknowledge_risk=payload.acknowledge_risk,
            actor=actor,
        )
    except PolicyError as exc:
        raise _refusal(exc, 404 if exc.code == "STRATEGY_NOT_FOUND" else 422) from exc
    return StrategyPolicyOut.model_validate(updated)
