"""The agent-order approval queue and the per-user automation tier.

An order attributed to an agent principal does not execute on its own while the
workspace's automation tier is ``manual``. It is claimed by its idempotency key, stored as
``pending_approval`` and held in this queue for a person. A person approves it — the stored
intent then runs through the one execution path — or rejects it with a reason. This module
owns the queue's lifecycle, the tier and the risk acknowledgement; it never executes an
order itself. :func:`ezchain_api.services.paper.approve_order` is the only caller that
turns an approval into a fill.

The tier is per user and defaults to ``manual``: a user with no row, an unrecognised tier,
or a ``full_auto`` row whose consent record is missing or unreadable all read as
``manual``. ``full_auto`` is only reachable by acknowledging the disclosure, and the
acknowledgement is an append-only row naming the user, the disclosure version and the
moment. A person, never an agent, changes the tier.

Every state change carries the principal that made it. A queue that cannot be read fails
closed with a typed reason code instead of letting an order through.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ezchain_core import (
    AUTOMATION_FULL,
    AUTOMATION_MANUAL,
    AUTOMATION_TIERS,
    DISCLOSURE_AUTOMATION_OPT_IN,
    AutomationSetting,
    OrderRequest,
    RiskDisclosure,
    RiskReason,
    utcnow,
)

from ..actor import ACTOR_HUMAN, OrderActor
from ..config import Settings
from ..models import (
    AutomationConsentRow,
    AutomationSettingRow,
    OrderApprovalRow,
    PaperOrderRow,
    new_id,
)

# The tier vocabulary and its consent rule live in ``ezchain_core.vault`` so the executor
# pipeline and this queue cannot disagree about what ``manual`` means or when ``full_auto``
# is consented. These are those strings, not a second set of literals.
Tier = Literal["manual", "full_auto"]
MANUAL: Tier = cast(Tier, AUTOMATION_MANUAL)
FULL_AUTO: Tier = cast(Tier, AUTOMATION_FULL)
TIERS: tuple[Tier, ...] = (MANUAL, FULL_AUTO)

# The pending window. The owner's policy is five minutes, and the setting bounds it.
DEFAULT_TTL_SECONDS = 300

# One declared lifecycle. ``pending`` is the only state that waits for a person;
# ``approved`` is a one-attempt claim that ends in ``executed`` or ``failed``. Every
# other state is terminal.
PENDING: Literal["pending"] = "pending"
APPROVED: Literal["approved"] = "approved"
REJECTED: Literal["rejected"] = "rejected"
EXPIRED: Literal["expired"] = "expired"
SUPERSEDED: Literal["superseded"] = "superseded"
EXECUTED: Literal["executed"] = "executed"
FAILED: Literal["failed"] = "failed"
APPROVAL_STATUSES: tuple[str, ...] = (
    PENDING,
    APPROVED,
    REJECTED,
    EXPIRED,
    SUPERSEDED,
    EXECUTED,
    FAILED,
)
# What a reader may do next in each state. A terminal state has no action.
NEXT_ACTIONS: dict[str, str] = {
    PENDING: "approve_or_reject",
    APPROVED: "execute",
    REJECTED: "none",
    EXPIRED: "none",
    SUPERSEDED: "none",
    EXECUTED: "none",
    FAILED: "none",
}
DISCLOSURE_VERSION = "v1"
DISCLOSURE_TEXT = (
    "You are letting orders execute without a person approving them. Your agent's orders "
    "will fill automatically, subject only to the platform's risk limits and the kill "
    "switch. You own the risk."
)


class ApprovalError(Exception):
    """A typed refusal from the queue. ``code`` is a stable reason code."""

    status_code = 409

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ApprovalNotFound(ApprovalError):
    status_code = 404

    def __init__(self) -> None:
        super().__init__("APPROVAL_NOT_FOUND", "approval not found")


class ApprovalRequiresPerson(ApprovalError):
    """Only a person may decide an approval or change the automation tier."""

    status_code = 403

    def __init__(self) -> None:
        super().__init__(
            "APPROVAL_REQUIRES_PERSON",
            "only a person may approve, reject or change the automation tier",
        )


class ApprovalStateUnreadable(ApprovalError):
    """The queue could not be read, so no order may be placed."""

    def __init__(self, detail: str) -> None:
        super().__init__("APPROVAL_STATE_UNREADABLE", detail)


class ApprovalIntentInvalid(ApprovalError):
    """The stored intent behind an approval cannot be rebuilt."""

    def __init__(self, detail: str = "the stored order is not a valid order request") -> None:
        super().__init__("APPROVAL_INTENT_INVALID", detail)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def ttl_seconds(settings: Settings) -> int:
    return int(getattr(settings, "approval_ttl_seconds", DEFAULT_TTL_SECONDS))


# ---------------------------------------------------------------------------
# The automation tier
# ---------------------------------------------------------------------------


def _consent_for(session: Session, user_id: str) -> AutomationConsentRow | None:
    return session.scalar(
        select(AutomationConsentRow)
        .where(
            AutomationConsentRow.user_id == user_id,
            AutomationConsentRow.disclosure_version == DISCLOSURE_VERSION,
        )
        .order_by(AutomationConsentRow.acknowledged_at.desc())
        .limit(1)
    )


def automation_setting(session: Session, settings: Settings) -> AutomationSetting:
    """This workspace's tier as the one core contract, never a local re-reading.

    ``ezchain_core.AutomationSetting`` refuses ``full_auto`` without an automation
    disclosure for the same user, so the same rule gates the executor pipeline and this
    queue. Anything the contract refuses — a missing row, an unrecognised tier string, a
    ``full_auto`` row whose consent record is absent or for another version — reads as a
    plain ``manual`` setting. The queue then holds the order; it never lets it through.
    """
    user = settings.workspace_id
    row = session.get(AutomationSettingRow, user)
    if row is None or row.tier not in AUTOMATION_TIERS or row.tier == MANUAL:
        return AutomationSetting(user=user, tier=MANUAL)
    consent = _consent_for(session, user)
    if consent is None:
        return AutomationSetting(user=user, tier=MANUAL)
    disclosure = RiskDisclosure(
        point=DISCLOSURE_AUTOMATION_OPT_IN,
        user=user,
        acknowledged_at=_aware(consent.acknowledged_at) or utcnow(),
        version=consent.disclosure_version,
    )
    try:
        return AutomationSetting(user=user, tier=FULL_AUTO, disclosure=disclosure)
    except ValueError:
        return AutomationSetting(user=user, tier=MANUAL)


def read_tier(session: Session, settings: Settings) -> Tier:
    """The tier in force for this workspace, as core's contract determines it."""
    return FULL_AUTO if automation_setting(session, settings).tier == FULL_AUTO else MANUAL


def automation_state(session: Session, settings: Settings) -> dict[str, Any]:
    """The tier and its provenance, for the read surface."""
    tier = read_tier(session, settings)
    consent = _consent_for(session, settings.workspace_id)
    row = session.get(AutomationSettingRow, settings.workspace_id)
    return {
        "user_id": settings.workspace_id,
        "tier": tier,
        "tiers": list(TIERS),
        "updated_at": None if row is None else _iso(row.updated_at),
        "updated_by": None
        if row is None or row.updated_by_kind is None
        else {"kind": row.updated_by_kind, "id": row.updated_by_id},
        "disclosure": {
            "version": DISCLOSURE_VERSION,
            "text": DISCLOSURE_TEXT,
            "acknowledged_at": None if consent is None else _iso(consent.acknowledged_at),
        },
        "agent_orders": "wait_for_a_person" if tier == MANUAL else "execute_automatically",
    }


def set_tier(
    session: Session,
    *,
    tier: str,
    actor: OrderActor,
    settings: Settings,
    acknowledge_risk: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Change the tier. Only a person may, and ``full_auto`` needs the acknowledgement.

    The acknowledgement is appended, never rewritten: withdrawing full-auto writes a
    ``manual`` setting and leaves the consent history intact. A refused change writes
    nothing at all.
    """
    if actor.kind != ACTOR_HUMAN:
        raise ApprovalRequiresPerson()
    if tier not in TIERS:
        raise ApprovalError(
            "AUTOMATION_TIER_UNKNOWN",
            f"tier must be one of {', '.join(TIERS)}",
        )
    stamp = now or utcnow()
    disclosure: RiskDisclosure | None = None
    if tier == FULL_AUTO:
        if not acknowledge_risk:
            raise ApprovalError(
                "AUTOMATION_CONSENT_REQUIRED",
                "switching to full_auto requires acknowledging the risk disclosure",
            )
        disclosure = RiskDisclosure(
            point=DISCLOSURE_AUTOMATION_OPT_IN,
            user=settings.workspace_id,
            acknowledged_at=stamp,
            version=DISCLOSURE_VERSION,
        )
    # Core's contract is the only judge of whether this tier is consented; nothing is
    # written if it refuses.
    try:
        AutomationSetting(user=settings.workspace_id, tier=tier, disclosure=disclosure)
    except ValueError as exc:
        raise ApprovalError("AUTOMATION_CONSENT_REQUIRED", str(exc)) from exc
    row = session.get(AutomationSettingRow, settings.workspace_id)
    if row is None:
        row = AutomationSettingRow(user_id=settings.workspace_id, tier=tier)
        session.add(row)
    row.tier = tier
    row.updated_at = stamp
    row.updated_by_kind = actor.kind
    row.updated_by_id = actor.id
    if tier == FULL_AUTO:
        session.add(
            AutomationConsentRow(
                id=new_id(),
                user_id=settings.workspace_id,
                tier=FULL_AUTO,
                disclosure_version=DISCLOSURE_VERSION,
                disclosure_text=DISCLOSURE_TEXT,
                actor_kind=actor.kind,
                actor_id=actor.id,
                acknowledged_at=stamp,
            )
        )
    session.commit()
    return automation_state(session, settings)


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------


def intent_to_dict(payload: OrderRequest) -> dict[str, Any]:
    """The stored intent: exactly the request the person is approving."""
    return {
        "chain": payload.chain,
        "token_address": payload.token_address,
        "side": payload.side,
        "amount_usd": str(payload.amount_usd),
        "idempotency_key": payload.idempotency_key,
        "strategy_id": payload.strategy_id,
        "quantity": None if payload.quantity is None else str(payload.quantity),
        "reduce_only": bool(payload.reduce_only),
        "risk_profile": payload.risk_profile,
    }


def intent_from_dict(value: Any) -> OrderRequest:
    """Rebuild the stored intent, failing closed on anything unusable.

    A stored order is executed from its own fields, never from a body sent with the
    decision, so this parser is the boundary that keeps an approval from executing
    something other than what the agent proposed.
    """
    if not isinstance(value, dict):
        raise ApprovalIntentInvalid("the stored intent is not an object")
    try:
        chain = str(value["chain"])
        token_address = str(value["token_address"])
        side = str(value["side"])
        amount = Decimal(str(value["amount_usd"]))
        key = str(value["idempotency_key"])
        quantity_raw = value.get("quantity")
        quantity = None if quantity_raw is None else Decimal(str(quantity_raw))
        strategy = value.get("strategy_id")
        profile = value.get("risk_profile")
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise ApprovalIntentInvalid() from exc
    if side not in ("buy", "sell") or not amount.is_finite() or amount <= 0:
        raise ApprovalIntentInvalid()
    if quantity is not None and (not quantity.is_finite() or quantity <= 0):
        raise ApprovalIntentInvalid()
    return OrderRequest(
        chain=chain,
        token_address=token_address,
        side=side,  # type: ignore[arg-type]
        amount_usd=amount,
        idempotency_key=key,
        strategy_id=None if strategy is None else str(strategy),
        quantity=quantity,
        reduce_only=bool(value.get("reduce_only", False)),
        risk_profile=None if profile is None else str(profile),
    )


def effective_status(row: OrderApprovalRow, now: datetime | None = None) -> str:
    """The status this row means right now, computed without writing.

    A read never mutates the queue, so a pending row past its window reports ``expired``
    here and transitions to it when a decision is attempted or a sweep runs. An
    unrecognised stored status reports ``failed``: it is never read as pending.
    """
    moment = now or utcnow()
    status = row.status
    if status not in APPROVAL_STATUSES:
        return FAILED
    if status == PENDING:
        expires = _aware(row.expires_at)
        if expires is not None and expires <= moment:
            return EXPIRED
    return status


def next_action(status: str) -> str:
    return NEXT_ACTIONS.get(status, "none")


def require_person(actor: OrderActor) -> None:
    """Refuse a decision from anything that is not a person.

    An agent key never approves or rejects, and neither does an engine order: only the
    deployment principal, recorded as ``human``, may decide. This is the rule that makes
    an agent approving its own proposal impossible, not a scope check alone.
    """
    if actor.kind != ACTOR_HUMAN:
        raise ApprovalRequiresPerson()


def to_dict(row: OrderApprovalRow, *, now: datetime | None = None) -> dict[str, Any]:
    """One approval as the API reports it."""
    status = effective_status(row, now)
    return {
        "id": row.id,
        "order_id": row.order_id,
        "status": status,
        "next_action": next_action(status),
        "intent": dict(row.intent or {}),
        "risk_decision": dict(row.risk_decision or {}),
        "execution_reason": row.execution_reason,
        "decided_by": None
        if row.decided_by_kind is None
        else {"kind": row.decided_by_kind, "id": row.decided_by_id},
        "decision_note": row.decision_note,
        "created_at": _iso(row.created_at),
        "expires_at": _iso(row.expires_at),
        "decided_at": _iso(row.decided_at),
        "executed_at": _iso(row.executed_at),
        "mode": "paper",
    }


def list_approvals(
    session: Session,
    *,
    status: str | None = None,
    limit: int = 50,
    now: datetime | None = None,
) -> list[OrderApprovalRow]:
    """Newest first. A ``status`` filter is applied to the effective status.

    The filter is evaluated in Python because expiry is a computed state, not a stored
    one, and a read never writes. Paper volumes make the bounded scan cheap.
    """
    rows = list(
        session.scalars(
            select(OrderApprovalRow).order_by(OrderApprovalRow.created_at.desc()).limit(limit)
        )
    )
    if status is None:
        return rows
    return [row for row in rows if effective_status(row, now) == status]


def get(session: Session, approval_id: str) -> OrderApprovalRow | None:
    return session.get(OrderApprovalRow, approval_id)


def by_order(session: Session, order_id: str) -> OrderApprovalRow | None:
    return session.scalar(select(OrderApprovalRow).where(OrderApprovalRow.order_id == order_id))


def by_orders(session: Session, order_ids: Sequence[str]) -> dict[str, OrderApprovalRow]:
    """Approvals keyed by order id, for one page of orders."""
    if not order_ids:
        return {}
    rows = session.scalars(
        select(OrderApprovalRow).where(OrderApprovalRow.order_id.in_(list(order_ids)))
    )
    return {row.order_id: row for row in rows}


def lock(session: Session, approval_id: str) -> OrderApprovalRow | None:
    """Read one approval for a decision, serialized against a concurrent decision.

    Postgres takes a row lock. SQLite ignores ``FOR UPDATE``, so a no-op update takes the
    database write lock instead; without it two approvers could both read ``pending``.
    """
    if session.get_bind().dialect.name == "sqlite":
        session.execute(
            update(OrderApprovalRow)
            .where(OrderApprovalRow.id == approval_id)
            .values(id=OrderApprovalRow.id)
        )
    else:
        session.execute(
            select(OrderApprovalRow.id).where(OrderApprovalRow.id == approval_id).with_for_update()
        )
    return session.get(OrderApprovalRow, approval_id)


def record_proposal(
    session: Session,
    order: PaperOrderRow,
    payload: OrderRequest,
    risk_decision: dict[str, Any],
    settings: Settings,
    *,
    now: datetime | None = None,
) -> OrderApprovalRow:
    """Store one pending approval for an agent order and hold the order for a person.

    ``risk_decision`` is the server's own pre-quote verdict. A newer proposal from the
    same agent for the same market and side supersedes an older pending one, so the queue
    shows one live proposal per intent instead of a stack of stale ones.
    """
    moment = now or utcnow()
    pending = list(
        session.scalars(
            select(OrderApprovalRow).where(
                OrderApprovalRow.status == PENDING,
                OrderApprovalRow.order_id != order.id,
            )
        )
    )
    for older in pending:
        older_order = session.get(PaperOrderRow, older.order_id)
        if older_order is None:
            continue
        if (
            older_order.chain != payload.chain
            or older_order.token_address != payload.token_address
            or older_order.side != payload.side
            or older_order.actor_kind != order.actor_kind
            or older_order.actor_id != order.actor_id
        ):
            continue
        older.status = SUPERSEDED
        older.execution_reason = "APPROVAL_SUPERSEDED"
        older.decided_at = moment
        older_order.status = "rejected"
        older_order.reason = "APPROVAL_SUPERSEDED"
        older_order.reasons = [RiskReason("APPROVAL_SUPERSEDED").to_dict()]

    row = OrderApprovalRow(
        id=new_id(),
        order_id=order.id,
        status=PENDING,
        intent=intent_to_dict(payload),
        risk_decision=risk_decision,
        created_at=moment,
        expires_at=moment + timedelta(seconds=ttl_seconds(settings)),
    )
    session.add(row)
    order.status = "pending_approval"
    order.reason = "AWAITING_APPROVAL"
    order.reasons = [RiskReason("AWAITING_APPROVAL").to_dict()]
    session.commit()
    session.refresh(row)
    return row


def claim_decision(
    session: Session,
    row: OrderApprovalRow,
    *,
    person: OrderActor,
    note: str | None = None,
    now: datetime | None = None,
) -> None:
    """Approve one pending row, marking the one attempt it authorises.

    Committed before execution so a second approver sees ``approved`` and is refused as a
    conflict. A process that dies between this commit and the fill leaves an approval that
    never executes: the order is not placed twice and nothing is guessed.
    """
    moment = now or utcnow()
    row.status = APPROVED
    row.decided_at = moment
    row.decided_by_kind = person.kind
    row.decided_by_id = person.id
    row.decision_note = note
    session.commit()
    session.refresh(row)


def refuse_decision(
    session: Session,
    row: OrderApprovalRow,
    *,
    person: OrderActor,
    note: str,
    now: datetime | None = None,
) -> None:
    """Reject one pending row and refuse its stored order with the person's reason."""
    moment = now or utcnow()
    row.status = REJECTED
    row.decided_at = moment
    row.decided_by_kind = person.kind
    row.decided_by_id = person.id
    row.decision_note = note
    order = session.get(PaperOrderRow, row.order_id)
    if order is not None:
        order.status = "rejected"
        order.reason = "APPROVAL_REJECTED"
        order.reasons = [
            RiskReason(
                "APPROVAL_REJECTED",
                detail={"note": note, "decided_by_kind": person.kind, "decided_by_id": person.id},
            ).to_dict()
        ]
    session.commit()
    session.refresh(row)


def expire(session: Session, row: OrderApprovalRow, *, now: datetime | None = None) -> None:
    """Move one pending row and its order to the expiry state."""
    moment = now or utcnow()
    row.status = EXPIRED
    row.decided_at = moment
    row.execution_reason = "APPROVAL_EXPIRED"
    order = session.get(PaperOrderRow, row.order_id)
    if order is not None:
        order.status = "rejected"
        order.reason = "APPROVAL_EXPIRED"
        order.reasons = [RiskReason("APPROVAL_EXPIRED").to_dict()]
    session.commit()
    session.refresh(row)


def expire_due(session: Session, settings: Settings, *, now: datetime | None = None) -> list[str]:
    """Expire every pending approval past its window, returning their ids.

    Called only from a write path (queueing a proposal or taking a decision), never from a
    read. A failure here is swallowed by the caller into a typed refusal: an unreadable
    queue must not place an order.
    """
    moment = now or utcnow()
    rows = list(session.scalars(select(OrderApprovalRow).where(OrderApprovalRow.status == PENDING)))
    expired: list[str] = []
    for row in rows:
        expires = _aware(row.expires_at)
        if expires is None or expires > moment:
            continue
        expire(session, row, now=moment)
        expired.append(row.id)
    return expired


def finish(
    session: Session,
    row: OrderApprovalRow,
    order: PaperOrderRow,
    *,
    now: datetime | None = None,
) -> None:
    """Record what the one approved execution attempt produced."""
    moment = now or utcnow()
    row.executed_at = moment
    if order.status == "filled":
        row.status = EXECUTED
        row.execution_reason = None
    else:
        row.status = FAILED
        row.execution_reason = order.reason or "EXECUTION_REFUSED"
    session.commit()
    session.refresh(row)


def fail_unexecuted(
    session: Session, row: OrderApprovalRow, code: str, *, now: datetime | None = None
) -> None:
    """Record a failure that happened before the order could produce a fill.

    The one attempt a decision authorised is spent, so the approval ends ``failed`` and the
    stored order is refused with the same reason: nothing is left pending, and a retry is
    a new order rather than a second firing of this approval.
    """
    moment = now or utcnow()
    row.status = FAILED
    row.execution_reason = code
    row.executed_at = moment
    order = session.get(PaperOrderRow, row.order_id)
    if order is not None and order.status == "pending_approval":
        order.status = "rejected"
        order.reason = code
        order.reasons = [RiskReason(code).to_dict()]
    session.commit()


@dataclass(frozen=True)
class QueueHealth:
    """Whether the queue can be read, for the placement path's fail-closed check."""

    readable: bool
    tier: Tier


def health(session: Session, settings: Settings) -> QueueHealth:
    """Probe the tier and the queue. An unreadable queue refuses the order.

    Every failure reads as unreadable, not just a database error: a queue whose state
    cannot be established has no safe default, and this is the check that makes the
    placement path fail closed rather than guess that an agent order may proceed.
    """
    try:
        tier = read_tier(session, settings)
        session.execute(select(OrderApprovalRow.id).limit(1)).first()
    except Exception:  # noqa: BLE001 - any failure to establish state is a refusal
        return QueueHealth(readable=False, tier=MANUAL)
    return QueueHealth(readable=True, tier=tier)


def _iso(value: datetime | None) -> str | None:
    moment = _aware(value)
    return None if moment is None else moment.isoformat()


__all__ = [
    "APPROVAL_STATUSES",
    "APPROVED",
    "ApprovalError",
    "ApprovalIntentInvalid",
    "ApprovalNotFound",
    "ApprovalRequiresPerson",
    "ApprovalStateUnreadable",
    "DEFAULT_TTL_SECONDS",
    "DISCLOSURE_TEXT",
    "DISCLOSURE_VERSION",
    "EXECUTED",
    "EXPIRED",
    "FAILED",
    "FULL_AUTO",
    "MANUAL",
    "NEXT_ACTIONS",
    "PENDING",
    "QueueHealth",
    "REJECTED",
    "SUPERSEDED",
    "TIERS",
    "Tier",
    "automation_state",
    "by_order",
    "by_orders",
    "claim_decision",
    "effective_status",
    "expire",
    "expire_due",
    "fail_unexecuted",
    "finish",
    "get",
    "health",
    "intent_from_dict",
    "intent_to_dict",
    "list_approvals",
    "lock",
    "next_action",
    "read_tier",
    "record_proposal",
    "refuse_decision",
    "require_person",
    "set_tier",
    "to_dict",
    "ttl_seconds",
]
