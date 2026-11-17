"""Permission modes for agent work, with one writer and one decision function.

Modes are a lifecycle, not three booleans. ``ask`` queues every configure or trade call,
``auto`` runs those inside the order limit and the agent's capped bucket, and ``read_only``
refuses them. The kill switch is checked first and overrides every mode: new risk is refused,
and a reduce-only exit is queued for a person so the agent cannot fill while the switch says
it is stopped.

The existing automation tier and its consent record remain the proof that a person opted in
to unattended execution. A mode never bypasses that consent: it can only make the gate
stricter. An unreadable or unknown mode fails closed to ``ask``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..actor import ACTOR_HUMAN, OrderActor
from ..config import Settings
from ..models import AgentPermissionDecisionRow, AgentPermissionModeRow
from .approvals import FULL_AUTO, ApprovalError, read_tier

Mode = Literal["ask", "auto", "read_only"]
Effect = Literal["read", "configure", "trade"]
Outcome = Literal["allow", "queue", "refuse"]

DEFAULT_MODE: Mode = "ask"
MODES: tuple[Mode, ...] = ("ask", "auto", "read_only")
STATES: dict[Mode, str] = {
    "ask": "Wait for a person before a configure or trade call.",
    "auto": "Run configure calls and trades inside the order limit and bucket.",
    "read_only": "Refuse every configure or trade call.",
}
TRANSITIONS: dict[Mode, tuple[Mode, ...]] = {
    "ask": ("auto", "read_only"),
    "auto": ("ask", "read_only"),
    "read_only": ("ask",),
}
ACTIONS: dict[Mode, str] = {
    "ask": "Approve or reject the queued call.",
    "auto": "Watch the spend; reduce the limit or switch to ask.",
    "read_only": "Switch to ask to allow calls again.",
}


class PermissionError(ApprovalError):
    """A permission-mode refusal, carried as a typed API reason code."""


def normalize_mode(value: object) -> Mode:
    """Read old or unknown values as the safe default; new writes use a canonical token."""
    return value if value in MODES else DEFAULT_MODE


def decide(
    *,
    mode: Mode,
    effect: Effect,
    limit_usd: Decimal,
    amount_usd: Decimal | None = None,
    kill_switch_active: bool = False,
    exit_only: bool = False,
) -> tuple[Outcome, str]:
    """The one decision function. It never reads state and never moves money.

    A reduce-only exit adds no risk, but the kill switch stops the agent: the exit is queued
    so a person still sees it instead of a stranded position, and never filled in auto. Any
    other refusal of an exit becomes a queue for the same reason.
    """
    if kill_switch_active and exit_only:
        return "queue", "KILL_SWITCH_ACTIVE"
    if kill_switch_active:
        return "refuse", "KILL_SWITCH_ACTIVE"
    if effect == "read":
        return "allow", "PERMISSION_READ"
    mode = normalize_mode(mode)
    outcome: Outcome
    code: str
    if mode == "read_only":
        outcome, code = "refuse", "PERMISSION_READ_ONLY"
    elif mode == "ask":
        outcome, code = "queue", "PERMISSION_ASK"
    elif effect == "configure":
        outcome, code = "allow", "PERMISSION_AUTO"
    elif amount_usd is not None and amount_usd > limit_usd:
        outcome, code = "queue", "PERMISSION_LIMIT_EXCEEDED"
    else:
        outcome, code = "allow", "PERMISSION_AUTO"
    if exit_only and outcome == "refuse":
        return "queue", code
    return outcome, code


def mode_for(session: Session, user: str, settings: Settings) -> Mode:
    """The stored mode, or the consented automation tier before one was ever set.

    A user who already opted into unattended execution keeps that behavior: with no mode
    row, ``auto`` follows a consented full-auto tier and everything else reads as ``ask``.
    Unreadable state fails closed to ``ask``.
    """
    try:
        row = session.get(AgentPermissionModeRow, user)
        if row is not None:
            return normalize_mode(row.mode)
        return "auto" if read_tier(session, settings, user) == FULL_AUTO else DEFAULT_MODE
    except Exception:  # noqa: BLE001 - a permission read must fail closed
        session.rollback()
        return DEFAULT_MODE


def limits(settings: Settings) -> dict[str, object]:
    """The limit an auto call is measured against, from the one config home."""
    return {
        "max_order_usd": str(settings.max_order_usd),
        "modes": list(MODES),
        "actions": dict(ACTIONS),
    }


def to_dict(session: Session, user: str, settings: Settings) -> dict[str, object]:
    return {
        "user_id": user,
        "mode": mode_for(session, user, settings),
        **limits(settings),
        "as_of": datetime.now().astimezone().isoformat(),
    }


def set_mode(
    session: Session,
    *,
    user: str,
    mode: str,
    actor: OrderActor,
    settings: Settings,
    now: datetime | None = None,
) -> dict[str, object]:
    """One writer. A person only; a mode unknown to the machine is refused."""
    if actor.kind != ACTOR_HUMAN:
        raise PermissionError("PERMISSION_REQUIRES_PERSON", "a person must change the mode")
    if mode not in MODES:
        raise PermissionError("PERMISSION_MODE_UNKNOWN", f"unknown mode {mode!r}")
    current = mode_for(session, user, settings)
    if mode != current and mode not in TRANSITIONS[current]:
        raise PermissionError("PERMISSION_TRANSITION_INVALID", f"cannot move {current} to {mode}")
    moment = now or datetime.now().astimezone()
    row = session.get(AgentPermissionModeRow, user)
    if row is None:
        row = AgentPermissionModeRow(
            user_id=user,
            mode=mode,
            updated_at=moment,
            updated_by_kind=actor.kind,
            updated_by_id=actor.id,
        )
        session.add(row)
    else:
        row.mode = mode
        row.updated_at = moment
        row.updated_by_kind = actor.kind
        row.updated_by_id = actor.id
    session.commit()
    return to_dict(session, user, settings)


def record_decision(
    session: Session,
    *,
    user: str,
    effect: Effect,
    mode: Mode,
    outcome: Outcome,
    code: str,
    amount_usd: Decimal | None = None,
    order_id: str | None = None,
    now: datetime | None = None,
) -> None:
    """Append one decision so a mode change to money is replayable from persisted inputs."""
    session.add(
        AgentPermissionDecisionRow(
            user_id=user,
            effect=effect,
            mode=mode,
            outcome=outcome,
            code=code,
            amount_usd=amount_usd,
            order_id=order_id,
            created_at=now or datetime.now().astimezone(),
        )
    )


def list_decisions(session: Session, user: str, limit: int = 50) -> list[dict[str, object]]:
    rows = session.scalars(
        select(AgentPermissionDecisionRow)
        .where(AgentPermissionDecisionRow.user_id == user)
        .order_by(AgentPermissionDecisionRow.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": row.id,
            "effect": row.effect,
            "mode": row.mode,
            "outcome": row.outcome,
            "code": row.code,
            "amount_usd": None if row.amount_usd is None else str(row.amount_usd),
            "order_id": row.order_id,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


__all__ = [
    "ACTIONS",
    "DEFAULT_MODE",
    "MODES",
    "STATES",
    "TRANSITIONS",
    "PermissionError",
    "decide",
    "limits",
    "list_decisions",
    "mode_for",
    "normalize_mode",
    "record_decision",
    "set_mode",
    "to_dict",
]
