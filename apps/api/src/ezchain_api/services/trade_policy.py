"""The trade-policy surface: one resolver, one read model, one per-strategy write.

The fields and the profile values come from ``ezchain_core.trade_policy``; sizing is
checked by ``size_reasons``, the same helper the order gate calls. Nothing here re-derives
a limit, so the page cannot show a number the gate would not apply.

A strategy may carry its own profile. The write is a server-side decision: the profile
name is validated, a change that loosens a fund-affecting limit needs an acknowledgement,
and the change is appended to the strategy's run log as an audit entry. Null inherits the
deployment default, so a strategy that never opts in keeps its current behaviour.

Precedence is the same here as at the order gate (``paper.resolve_order_profile``), and it
is stated once: a strategy's pinned profile is the floor on risk for its orders, a
per-order ``risk_profile`` may only tighten it (a looser one on an entry is refused as
``PROFILE_LOOSENS_STRATEGY``), and ``settings.trade_risk_profile`` is the default when
neither names one. "No profile" therefore means the deployment's own limits, not the
fewest gates, which is what the loosening test compares against.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_core import TRADE_RISK_PROFILES
from ezchain_core.trade_policy import (
    PROFILE_ORDER,
    PolicyError,
    SizingPreview,
    preview_size,
    profile_document,
    profile_for,
)

from ..config import Settings
from ..models import StrategyRow, StrategyRunRow

AUDIT_REASON = "TRADE_POLICY_UPDATED"


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _wire(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _effective(row: StrategyRow, settings: Settings) -> str | None:
    return row.risk_profile if row.risk_profile is not None else settings.trade_risk_profile


def warnings_for(row: StrategyRow, settings: Settings) -> list[dict[str, Any]]:
    """The strategy's own sizing that its profile would refuse, named honestly."""
    warnings: list[dict[str, Any]] = []
    profile = profile_for(_effective(row, settings))
    if profile is None:
        return warnings
    params = row.params or {}
    order_usd = _decimal(params.get("order_usd"))
    max_position = _decimal(params.get("max_position_usd"))
    if order_usd is not None:
        if order_usd < profile.min_order_usd:
            warnings.append(
                {
                    "code": "SIZE_BELOW_MIN",
                    "message": "the strategy's order size is below the profile minimum",
                    "detail": {"limit": str(profile.min_order_usd), "value": str(order_usd)},
                }
            )
        if order_usd > profile.max_order_usd:
            warnings.append(
                {
                    "code": "SIZE_ABOVE_MAX",
                    "message": "the strategy's order size is above the profile maximum",
                    "detail": {"limit": str(profile.max_order_usd), "value": str(order_usd)},
                }
            )
    if max_position is not None:
        if max_position > settings.max_position_usd:
            warnings.append(
                {
                    "code": "POSITION_LIMIT",
                    "message": "the strategy's position cap is above the deployment cap",
                    "detail": {
                        "limit": str(settings.max_position_usd),
                        "value": str(max_position),
                    },
                }
            )
        if max_position > profile.per_token_cap_usd:
            warnings.append(
                {
                    "code": "POSITION_LIMIT",
                    "message": "the strategy's position cap is above the profile per-token cap",
                    "detail": {
                        "limit": str(profile.per_token_cap_usd),
                        "value": str(max_position),
                    },
                }
            )
        if order_usd is not None and max_position < order_usd:
            warnings.append(
                {
                    "code": "POLICY_INVALID",
                    "message": "the strategy's position cap is below its order size",
                    "detail": {
                        "order_usd": str(order_usd),
                        "max_position_usd": str(max_position),
                    },
                }
            )
    return warnings


def strategy_row(row: StrategyRow, settings: Settings) -> dict[str, Any]:
    params = row.params or {}
    effective = _effective(row, settings)
    return {
        "id": row.id,
        "name": row.name,
        "token_symbol": row.token_symbol,
        "risk_profile": row.risk_profile,
        "effective_profile": effective,
        "source": "strategy" if row.risk_profile is not None else "default",
        "order_usd": _wire(_decimal(params.get("order_usd"))),
        "max_position_usd": _wire(_decimal(params.get("max_position_usd"))),
        "warnings": warnings_for(row, settings),
    }


def describe(session: Session, settings: Settings) -> dict[str, Any]:
    """Every declared profile, the active one, and each strategy's effective policy."""
    profiles = [profile_document(TRADE_RISK_PROFILES[name]) for name in PROFILE_ORDER]
    strategies = session.scalars(select(StrategyRow).order_by(StrategyRow.created_at.desc())).all()
    active = settings.trade_risk_profile
    active_profile = profile_for(active)
    fields = (
        []
        if active_profile is None
        else cast("list[dict[str, object]]", profile_document(active_profile)["fields"])
    )
    return {
        "mode": "paper",
        "active_profile": active,
        "profiles": profiles,
        "fields": fields,
        "strategies": [strategy_row(row, settings) for row in strategies],
    }


def preview(
    profile_name: str | None,
    amount_usd: Decimal,
    equity_usd: Decimal | None,
) -> dict[str, Any]:
    """The server's sizing verdict, computed by the order gate's own resolver."""
    result: SizingPreview = preview_size(profile_name, amount_usd, equity_usd)
    return {
        "allowed": result.allowed,
        "profile": result.profile,
        "amount_usd": str(result.amount_usd),
        "equity_usd": _wire(result.equity_usd),
        "limits": {
            "min_order_usd": str(result.limits.min_order_usd),
            "max_order_usd": str(result.limits.max_order_usd),
            "max_order_pct_of_equity": str(result.limits.max_order_pct_of_equity),
            "per_token_cap_usd": str(result.limits.per_token_cap_usd),
            "max_slippage_bps": result.limits.max_slippage_bps,
        },
        "percent_cap_usd": _wire(result.percent_cap_usd),
        "effective_max_usd": str(result.effective_max_usd),
        "reasons": [reason.to_dict() for reason in result.reasons],
    }


def _order_ceiling(name: str | None, settings: Settings) -> Decimal:
    """The absolute per-order ceiling the gate enforces for this profile, or the deployment.

    ``None`` is not "no gate": it is the deployment's own ``settings.max_order_usd``, which
    is what an order is measured against when no profile shapes it. A declared profile's
    ceiling replaces that value rather than adding to it (``paper.effective_max_order_usd``),
    which is the one bound a profile can widen.
    """
    profile = profile_for(name)
    return settings.max_order_usd if profile is None else profile.max_order_usd


def _loosens(before: str | None, after: str | None, settings: Settings) -> bool:
    """Whether moving to ``after`` admits a larger order than ``before``.

    The comparison is of the enforced order ceilings, with ``None`` meaning the
    deployment's own limits — because a strategy with no profile is already bounded by
    them. So opting into ``high`` ($250) where the deployment caps orders at $100 widens
    the order a strategy may place and needs the acknowledgement.

    Every other profile field only *adds* a gate: a minimum size, a percent-of-equity
    ceiling and a per-token cap are absent from a deployment with no profile, and the
    deployment's ``max_position_usd`` and ``max_price_impact_pct`` keep applying beside a
    profile, so neither can be widened by one.
    """
    return _order_ceiling(after, settings) > _order_ceiling(before, settings)


def update_strategy(
    session: Session,
    settings: Settings,
    strategy_id: str,
    *,
    risk_profile: str | None,
    acknowledge_risk: bool,
    actor: str | None = None,
) -> dict[str, Any]:
    """Persist one strategy's profile override and append an audit entry.

    ``None`` clears the override; the deployment default applies again. A profile the
    registry does not define is ``PROFILE_UNKNOWN``, never a silent fallback.

    A change that admits a larger order than the strategy's current effective policy needs
    ``acknowledge_risk``; the comparison is of the enforced order ceilings, with ``None``
    meaning the deployment's own ``max_order_usd`` (see :func:`_loosens`).

    The audit entry carries the stored override before and after, the effective profile
    each resolved to, the principal that made the change and whether the loosening was
    acknowledged, so the entry is the whole decision rather than a truncated name.
    """
    row = session.get(StrategyRow, strategy_id)
    if row is None:
        raise PolicyError("STRATEGY_NOT_FOUND", f"strategy {strategy_id} not found")
    profile_for(risk_profile)  # raises PROFILE_UNKNOWN before anything is written

    before = _effective(row, settings)
    after = risk_profile if risk_profile is not None else settings.trade_risk_profile
    loosens = _loosens(before, after, settings)
    if loosens and not acknowledge_risk:
        raise PolicyError(
            "RISK_ACK_REQUIRED",
            (
                f"changing this strategy to the {after!r} profile loosens a limit that "
                "governs your own funds; resend with acknowledge_risk=true to confirm"
            ),
        )

    previous = row.risk_profile
    row.risk_profile = risk_profile
    session.add(
        StrategyRunRow(
            strategy_id=row.id,
            action="hold",
            reason=AUDIT_REASON,
            inputs={
                "before": previous,
                "after": risk_profile,
                "before_effective": before,
                "after_effective": after,
                "loosens": loosens,
                "acknowledged": acknowledge_risk,
                "actor": actor,
            },
        )
    )
    session.commit()
    session.refresh(row)
    return strategy_row(row, settings)
