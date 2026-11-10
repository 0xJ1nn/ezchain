"""Protection service: validated CRUD and profile resolution for paper arms.

Validation lives in core; this module owns the rows, the resolution seam
(per-chain overrides global, profile defaults fill missing arm params) and the
open-position check. The engine owns evaluation; this service never quotes or
journals.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from ezchain_core.ledger import position_account, replay_position
from ezchain_core.protection import (
    DEFAULT_STOP_LOSS_PCT,
    DEFAULT_TAKE_PROFIT_PCT,
    DEFAULT_TRAILING_PCT,
    ProtectionProfile,
    ProtectionValidationError,
    resolve_profile,
    validate_arm,
    validate_profile,
)
from ezchain_core.types import utcnow as core_now

from ..models import (
    LedgerEntryRow,
    ProtectionArmRow,
    ProtectionEventRow,
    ProtectionProfileRow,
    new_id,
    next_protection_event_seq,
)


class ProtectionServiceError(ValueError):
    """A 409-style conflict: the row exists but the action is not allowed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _parse_expiry(value: str | None) -> datetime | None:
    if value is None or not str(value).strip():
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProtectionValidationError(
            "PROTECTION_PARAM_TYPE", "expires_at must be an ISO timestamp"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _position_quantity(session: Session, chain: str, token_address: str) -> Decimal:
    rows = session.scalars(
        select(LedgerEntryRow)
        .where(LedgerEntryRow.account == position_account(chain, token_address))
        .order_by(LedgerEntryRow.created_at, LedgerEntryRow.id)
    ).all()
    from ezchain_core.ledger import LedgerEntry

    entries = (
        LedgerEntry(
            account=r.account,
            asset=r.asset,
            amount=Decimal(str(r.amount)),
            usd_value=Decimal(str(r.usd_value)),
            kind=r.kind,
        )
        for r in rows
    )
    quantity, _ = replay_position(entries)
    return quantity


def _profile_from_row(row: ProtectionProfileRow) -> ProtectionProfile:
    params = row.params or {}
    return ProtectionProfile(
        id=row.id,
        name=row.name,
        chain=row.chain,
        kind=row.kind,  # type: ignore[arg-type]
        stop_loss_pct=Decimal(str(params["stop_loss_pct"]))
        if params.get("stop_loss_pct") is not None
        else None,
        trailing_pct=Decimal(str(params["trailing_pct"]))
        if params.get("trailing_pct") is not None
        else None,
        take_profit_pct=Decimal(str(params["take_profit_pct"]))
        if params.get("take_profit_pct") is not None
        else None,
        rules=tuple(row.rules or []),
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


def _profile_defaults(profile: ProtectionProfile | None, kind: str) -> dict[str, Any]:
    """Fill the arm's percent basis from the profile when the caller left it blank."""
    if profile is None:
        return {}
    if kind == "stop_loss" and profile.stop_loss_pct is not None:
        return {"loss_pct": str(profile.stop_loss_pct)}
    if kind == "trailing_stop" and profile.trailing_pct is not None:
        return {"trailing_pct": str(profile.trailing_pct)}
    if kind == "take_profit" and profile.take_profit_pct is not None:
        return {"gain_pct": str(profile.take_profit_pct)}
    return {}


def resolve_profile_row(session: Session, chain: str) -> ProtectionProfileRow | None:
    rows = session.scalars(select(ProtectionProfileRow)).all()
    resolved = resolve_profile([_profile_from_row(r) for r in rows], chain)
    if resolved is None:
        return None
    return session.get(ProtectionProfileRow, resolved.id)


def record_event(
    session: Session,
    *,
    arm_id: str,
    action: str,
    reason: str | None = None,
    rule_snapshot: dict[str, Any] | None = None,
    mark: Decimal | None = None,
    observed_at: datetime | None = None,
    order_id: str | None = None,
    dedupe: bool = False,
) -> ProtectionEventRow | None:
    """Append one audit row in the caller's transaction.

    ``dedupe`` suppresses a repeat of the immediately preceding transition:
    a hold that stays on the same reason, mark and observation time for a
    thousand ticks is one row, not a thousand. A moved mark or observation is
    a new fact and appends. Triggers, arms, updates and cancels always append.
    """
    observation = observed_at if observed_at is not None else core_now()
    if dedupe:
        last = session.scalars(
            select(ProtectionEventRow)
            .where(ProtectionEventRow.arm_id == arm_id)
            .order_by(ProtectionEventRow.seq.desc())
            .limit(1)
        ).first()
        if (
            last is not None
            and last.action == action
            and last.reason == reason
            and last.mark == mark
            and _aware(last.observed_at) == _aware(observation)
        ):
            return None
    row = ProtectionEventRow(
        arm_id=arm_id,
        action=action,
        reason=reason,
        rule_snapshot=None if rule_snapshot is None else dict(rule_snapshot),
        mark=mark,
        observed_at=observation,
        order_id=order_id,
        # The per-arm lock is taken before MAX(seq) is read, so two writers
        # cannot choose the same sequence and roll back one arm edit.
        seq=next_protection_event_seq(session, arm_id),
    )
    session.add(row)
    return row


def list_events(session: Session, arm_id: str, *, limit: int = 200) -> list[ProtectionEventRow]:
    """Newest first by the per-arm sequence, so equal timestamps never reorder."""
    return list(
        session.scalars(
            select(ProtectionEventRow)
            .where(ProtectionEventRow.arm_id == arm_id)
            .order_by(ProtectionEventRow.seq.desc())
            .limit(limit)
        ).all()
    )


def event_to_dict(row: ProtectionEventRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "arm_id": row.arm_id,
        "seq": int(row.seq),
        "action": row.action,
        "reason": row.reason,
        "rule_snapshot": row.rule_snapshot,
        "mark": None if row.mark is None else str(row.mark),
        "observed_at": _dt(row.observed_at) or "",
        "order_id": row.order_id,
        "created_at": _dt(row.created_at),
    }


def create_arm(
    session: Session,
    *,
    chain: str,
    token_address: str,
    token_symbol: str,
    kind: str,
    params: dict[str, Any] | None,
    profile_id: str | None = None,
    expires_at: str | None = None,
) -> ProtectionArmRow:
    """Arm protection for an open position. Fails closed when none is held."""
    if _position_quantity(session, chain, token_address) <= 0:
        raise ProtectionValidationError("NO_POSITION", "no open position to protect")
    merged = dict(params or {})
    profile: ProtectionProfileRow | None = None
    if profile_id is not None:
        profile = session.get(ProtectionProfileRow, profile_id)
        if profile is None:
            raise ProtectionValidationError("PROFILE_NOT_FOUND", "unknown profile")
    elif merged.get("levels") is None:
        # Implicit resolution: the per-chain profile overrides the global one.
        profile = resolve_profile_row(session, chain)
    if profile is not None:
        domain_profile = _profile_from_row(profile)
        for key, value in _profile_defaults(domain_profile, kind).items():
            merged.setdefault(key, value)
    validate_arm(kind, merged)
    row = ProtectionArmRow(
        id=new_id(),
        chain=chain,
        token_address=token_address,
        token_symbol=token_symbol,
        kind=kind,
        status="armed",
        params=merged,
        profile_id=profile.id if profile is not None else None,
        expires_at=_parse_expiry(expires_at),
    )
    session.add(row)
    record_event(
        session,
        arm_id=row.id,
        action="armed",
        reason=None,
        rule_snapshot=dict(merged),
        mark=None,
        observed_at=core_now(),
        order_id=None,
    )
    session.commit()
    session.refresh(row)
    return row


def list_arms(
    session: Session,
    *,
    status: str | None = None,
    chain: str | None = None,
    token_address: str | None = None,
) -> list[ProtectionArmRow]:
    query = select(ProtectionArmRow).order_by(ProtectionArmRow.created_at.desc())
    if status:
        query = query.where(ProtectionArmRow.status == status)
    if chain:
        query = query.where(ProtectionArmRow.chain == chain)
    if token_address:
        query = query.where(ProtectionArmRow.token_address == token_address)
    return list(session.scalars(query).all())


def get_arm(session: Session, arm_id: str) -> ProtectionArmRow | None:
    return session.get(ProtectionArmRow, arm_id)


def patch_arm(
    session: Session,
    arm_id: str,
    params: dict[str, Any],
    expected_version: int | None = None,
) -> ProtectionArmRow:
    """Replace an armed arm's rule and append the before/after snapshot.

    ``expected_version`` is optional so existing external callers keep working;
    callers that supply it get compare-and-swap semantics and a 409 when they
    lost the race. Requiring the precondition for every caller is a separate
    contract change.
    """
    row = get_arm(session, arm_id)
    if row is None:
        raise ProtectionServiceError("ARM_NOT_FOUND", "protection arm not found")
    if expected_version is not None and int(row.version) != int(expected_version):
        raise ProtectionServiceError(
            "PROTECTION_CONFLICT",
            "protection arm version mismatch",
        )
    if row.status != "armed":
        raise ProtectionServiceError("ARM_NOT_EDITABLE", f"a {row.status} arm cannot be edited")
    validate_arm(row.kind, params)
    old_params = dict(row.params or {})
    now = core_now()
    if expected_version is None:
        # Even without a caller precondition, the version is a database-relative
        # increment: never write back the version this session loaded.
        result = cast(
            CursorResult[Any],
            session.execute(
                update(ProtectionArmRow)
                .where(
                    ProtectionArmRow.id == arm_id,
                    ProtectionArmRow.status == "armed",
                )
                .values(
                    params=dict(params),
                    version=ProtectionArmRow.version + 1,
                    updated_at=now,
                )
            ),
        )
        if result.rowcount != 1:
            session.rollback()
            raise ProtectionServiceError(
                "ARM_NOT_EDITABLE",
                "protection arm is no longer editable",
            )
        session.refresh(row)
    else:
        # Compare-and-swap, not read-check-write: two writers that both saw the
        # same version cannot both win. The loser gets a 409 below.
        result = cast(
            CursorResult[Any],
            session.execute(
                update(ProtectionArmRow)
                .where(
                    ProtectionArmRow.id == arm_id,
                    ProtectionArmRow.version == int(expected_version),
                    ProtectionArmRow.status == "armed",
                )
                .values(
                    params=dict(params),
                    version=ProtectionArmRow.version + 1,
                    updated_at=now,
                )
            ),
        )
        if result.rowcount != 1:
            session.rollback()
            raise ProtectionServiceError(
                "PROTECTION_CONFLICT",
                "protection arm version mismatch",
            )
        session.refresh(row)
    # An update is a new rule snapshot; the trail keeps both sides of the change
    # so a reader can reconstruct the rule that was in force before it.
    record_event(
        session,
        arm_id=row.id,
        action="updated",
        reason=None,
        rule_snapshot={"before": old_params, "after": dict(row.params or {})},
        mark=None,
        observed_at=now,
        order_id=row.order_id,
    )
    session.commit()
    session.refresh(row)
    return row


def cancel_arm(
    session: Session, arm_id: str, expected_version: int | None = None
) -> ProtectionArmRow:
    row = get_arm(session, arm_id)
    if row is None:
        raise ProtectionServiceError("ARM_NOT_FOUND", "protection arm not found")
    if expected_version is not None and int(row.version) != int(expected_version):
        raise ProtectionServiceError(
            "PROTECTION_CONFLICT",
            "protection arm version mismatch",
        )
    if row.status == "cancelled":
        return row
    if row.status in ("triggered", "expired"):
        raise ProtectionServiceError(
            "ARM_NOT_CANCELLABLE", f"a {row.status} arm cannot be cancelled"
        )
    now = core_now()
    if expected_version is None:
        result = cast(
            CursorResult[Any],
            session.execute(
                update(ProtectionArmRow)
                .where(
                    ProtectionArmRow.id == arm_id,
                    ProtectionArmRow.status == "armed",
                )
                .values(
                    status="cancelled",
                    version=ProtectionArmRow.version + 1,
                    updated_at=now,
                )
            ),
        )
        if result.rowcount != 1:
            session.rollback()
            raise ProtectionServiceError(
                "ARM_NOT_CANCELLABLE",
                "protection arm is no longer cancellable",
            )
        session.refresh(row)
    else:
        result = cast(
            CursorResult[Any],
            session.execute(
                update(ProtectionArmRow)
                .where(
                    ProtectionArmRow.id == arm_id,
                    ProtectionArmRow.version == int(expected_version),
                    ProtectionArmRow.status == "armed",
                )
                .values(
                    status="cancelled",
                    version=ProtectionArmRow.version + 1,
                    updated_at=now,
                )
            ),
        )
        if result.rowcount != 1:
            session.rollback()
            raise ProtectionServiceError(
                "PROTECTION_CONFLICT",
                "protection arm version mismatch",
            )
        session.refresh(row)
    record_event(
        session,
        arm_id=row.id,
        action="cancelled",
        reason=None,
        rule_snapshot=dict(row.params or {}),
        mark=None,
        observed_at=now,
        order_id=row.order_id,
    )
    session.commit()
    session.refresh(row)
    return row


def create_profile(
    session: Session,
    *,
    name: str,
    chain: str | None,
    kind: str,
    params: dict[str, Any] | None,
    rules: list[str] | None,
) -> ProtectionProfileRow:
    """Create a reusable preset. Smart profiles must carry inspectable rules."""
    stop_loss, trailing, take_profit, clean_rules = validate_profile(
        name, chain, kind, params or {}, rules or []
    )
    stored: dict[str, str] = {}
    if stop_loss is not None:
        stored["stop_loss_pct"] = str(stop_loss)
    if trailing is not None:
        stored["trailing_pct"] = str(trailing)
    if take_profit is not None:
        stored["take_profit_pct"] = str(take_profit)
    row = ProtectionProfileRow(
        id=new_id(),
        name=name,
        chain=chain,
        kind=kind,
        params=stored,
        rules=list(clean_rules),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def list_profiles(session: Session, *, chain: str | None = None) -> list[ProtectionProfileRow]:
    query = select(ProtectionProfileRow).order_by(ProtectionProfileRow.updated_at.desc())
    if chain is not None:
        query = query.where(ProtectionProfileRow.chain == chain)
    return list(session.scalars(query).all())


def _dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    moment = _aware(value)
    assert moment is not None
    return moment.isoformat()


def arm_to_dict(row: ProtectionArmRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "chain": row.chain,
        "token_address": row.token_address,
        "token_symbol": row.token_symbol,
        "kind": row.kind,
        "status": row.status,
        "params": row.params or {},
        "peak_usd": None if row.peak_usd is None else str(row.peak_usd),
        "triggered_levels": list(row.triggered_levels or []),
        "triggered_at": _dt(row.triggered_at),
        "order_id": row.order_id,
        "expires_at": _dt(row.expires_at),
        "profile_id": row.profile_id,
        "version": int(row.version or 1),
        "created_at": _dt(row.created_at) or "",
        "updated_at": _dt(row.updated_at) or "",
        "mode": "paper",
    }


def profile_to_dict(row: ProtectionProfileRow) -> dict[str, Any]:
    params = row.params or {}
    return {
        "id": row.id,
        "name": row.name,
        "chain": row.chain,
        "kind": row.kind,
        "stop_loss_pct": None
        if params.get("stop_loss_pct") is None
        else str(params["stop_loss_pct"]),
        "trailing_pct": None if params.get("trailing_pct") is None else str(params["trailing_pct"]),
        "take_profit_pct": None
        if params.get("take_profit_pct") is None
        else str(params["take_profit_pct"]),
        "rules": list(row.rules or []),
        "created_at": _dt(row.created_at) or "",
        "updated_at": _dt(row.updated_at) or "",
        "mode": "paper",
    }


# Keep the domain defaults beside their single home for callers that need them.
__all__ = [
    "DEFAULT_STOP_LOSS_PCT",
    "DEFAULT_TAKE_PROFIT_PCT",
    "DEFAULT_TRAILING_PCT",
]
