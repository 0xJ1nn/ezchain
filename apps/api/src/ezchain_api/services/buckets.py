"""An agent's capped bucket: the cap setting and its ledger-derived deployment.

An agent order must draw only from cash its owner set aside for that agent. The bucket is
not a second account and has no balance column: the cap is one row per agent, and what the
agent has deployed is the net cash its journal legs moved, replayed from
``ledger_entries`` (CONTRACT I1). Every cash leg an agent's order writes is attributed to
the bucket id, so the deployment survives a restart, cannot drift from the journal, and
cannot be edited by writing a number.

The gate the order service runs is :func:`check` — arithmetic in
``ezchain_core.bucket`` — and it is **entry-only**. Exits are never refused: a cap may not
trap a position, so lowering a cap below what is already deployed refuses new buys while
every sell still settles and pays cash back into the bucket.

Fail closed. A bucket state that cannot be read refuses the entry with
``AGENT_BUCKET_UNREADABLE``; an unset cap is not an unreadable one — an agent whose owner
has set no cap is governed by the account's own gates, exactly as before this module.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_core import (
    BUCKET_CAP_INVALID,
    BUCKET_REQUIRES_PERSON,
    BUCKET_UNREADABLE,
    BucketState,
    bucket_state,
    check_bucket,
    utcnow,
)
from ezchain_core.types import RiskReason

from ..actor import ACTOR_AGENT, ACTOR_HUMAN, ACTOR_STRATEGY, OrderActor
from ..models import AgentBucketRow, ApiKeyRow, StrategyRow

MAX_CAP_USD = Decimal("1000000000")


class BucketError(Exception):
    """A typed refusal from the bucket surface. ``code`` is a stable reason code."""

    status_code = 409

    def __init__(self, code: str, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        if status_code is not None:
            self.status_code = status_code


class BucketRequiresPerson(BucketError):
    status_code = 403

    def __init__(self, message: str = "only the agent's owner may set its bucket cap") -> None:
        super().__init__(BUCKET_REQUIRES_PERSON, message, 403)


class BucketCapInvalid(BucketError):
    status_code = 422

    def __init__(self, message: str = "an agent's bucket cap must be zero or greater") -> None:
        super().__init__(BUCKET_CAP_INVALID, message, 422)


class BucketUnreadable(BucketError):
    status_code = 409

    def __init__(self, message: str = "the agent bucket could not be read") -> None:
        super().__init__(BUCKET_UNREADABLE, message, 409)


def agent_bucket_key(session: Session, actor: OrderActor) -> str | None:
    """The bucket an order draws from, or ``None`` when it draws from no bucket.

    A direct agent order is addressed by its key id, which is the bucket key. A strategy
    order is addressed by the strategy, so the bucket is the key of the agent that created
    it: an agent cannot escape its cap by wrapping the same automation in a strategy. A
    person's order, and a strategy a person created, draw from no bucket.
    """
    if actor.kind == ACTOR_AGENT:
        return actor.id
    if actor.kind == ACTOR_STRATEGY and actor.id is not None:
        strategy = session.get(StrategyRow, actor.id)
        if strategy is None or strategy.created_by_kind != ACTOR_AGENT:
            return None
        return strategy.created_by_id
    return None


def cap_for(session: Session, agent_id: str) -> Decimal | None:
    """The stored cap, or ``None`` when this agent's owner has set none.

    An unreadable row is not an unset one: the caller turns a raised error into
    ``AGENT_BUCKET_UNREADABLE`` rather than reading it as unlimited.
    """
    row = session.get(AgentBucketRow, agent_id)
    return None if row is None else Decimal(str(row.cap_usd))


def state_for(session: Session, agent_id: str, bucket_cash_usd: Decimal) -> BucketState:
    """The cap and the replayed deployment for one agent."""
    return bucket_state(
        agent_id, cap_usd=cap_for(session, agent_id), bucket_cash_usd=bucket_cash_usd
    )


def state_from_row(row: AgentBucketRow, bucket_cash_usd: Decimal) -> BucketState:
    """The same state, from a row the caller already holds (one query, not two)."""
    return bucket_state(
        row.agent_id, cap_usd=Decimal(str(row.cap_usd)), bucket_cash_usd=bucket_cash_usd
    )


def view(row: AgentBucketRow, bucket_cash_usd: Decimal) -> dict[str, Any]:
    """One bucket as the read surface reports it, with its stored provenance."""
    return {
        **state_from_row(row, bucket_cash_usd).to_dict(),
        "owner": row.owner,
        "updated_at": None if row.updated_at is None else _iso(row.updated_at),
        "updated_by": None
        if row.updated_by_kind is None
        else {"kind": row.updated_by_kind, "id": row.updated_by_id},
    }


def check(
    session: Session,
    agent_id: str | None,
    bucket_cash_usd: Decimal,
    requested_usd: Decimal,
    *,
    fee_usd: Decimal = Decimal(0),
) -> RiskReason | None:
    """The refusal for this entry, or ``None`` when it fits (entry-only).

    ``bucket_cash_usd`` is the replayed cash attributed to the bucket, so the caller passes
    the same ``Replay`` it already read rather than making this module re-scan the journal.
    Anything that raises while reading the cap is returned as ``AGENT_BUCKET_UNREADABLE``:
    the order service never sees a raw database error on the money path.
    """
    if agent_id is None:
        return None
    try:
        state = state_for(session, agent_id, bucket_cash_usd)
    except Exception:  # noqa: BLE001 - an unreadable bucket must refuse the entry
        # The persisted refusal names the agent, never the driver's own message: an order
        # row is read by the UI and the CSV export, and a database error can carry a DSN.
        return RiskReason(BUCKET_UNREADABLE, detail={"agent_id": agent_id})
    return check_bucket(state, requested_usd, fee_usd=fee_usd)


def set_cap(
    session: Session,
    agent_id: str,
    cap_usd: Decimal,
    *,
    actor: OrderActor,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Set an agent's cap. Only the owning person may, and nothing is written on refusal.

    Ownership comes from the agent key when it exists, so one user cannot move another
    user's money boundary. When no key row exists yet (the owner provisions a cap before
    minting the key), the first setting person becomes the owner and a later change must
    come from that same user.
    """
    if actor.kind != ACTOR_HUMAN:
        raise BucketRequiresPerson()
    cap = Decimal(str(cap_usd))
    if cap < 0:
        raise BucketCapInvalid()
    if cap > MAX_CAP_USD:
        raise BucketCapInvalid("an agent's bucket cap is too large")
    person = actor.principal
    if not person:
        raise BucketRequiresPerson()

    key = session.get(ApiKeyRow, agent_id)
    row = session.get(AgentBucketRow, agent_id)
    if key is not None and key.subject != person:
        raise BucketRequiresPerson("only the agent's owner may set its bucket cap")
    if row is not None and row.owner != person:
        raise BucketRequiresPerson("only the agent's owner may set its bucket cap")

    stamp = now or utcnow()
    if row is None:
        row = AgentBucketRow(agent_id=agent_id, owner=person, cap_usd=cap)
        session.add(row)
    row.cap_usd = cap
    row.owner = person
    row.updated_at = stamp
    row.updated_by_kind = actor.kind
    row.updated_by_id = actor.id
    session.commit()
    return {
        "agent_id": agent_id,
        "cap_usd": str(cap),
        "owner": row.owner,
        "updated_at": _iso(row.updated_at),
        "updated_by": {"kind": row.updated_by_kind, "id": row.updated_by_id},
    }


def list_buckets(session: Session, bucket_cash: dict[str, Decimal]) -> list[dict[str, Any]]:
    """Every capped agent, with its cap and the deployment replayed from the ledger.

    Only agents whose owner set a cap have a row; an uncapped agent is absent rather than
    reported as a zero cap, because a zero cap would mean "may deploy nothing".
    """
    rows = list(session.scalars(select(AgentBucketRow).order_by(AgentBucketRow.agent_id)))
    return [view(row, bucket_cash.get(row.agent_id, Decimal(0))) for row in rows]


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


__all__ = [
    "BucketCapInvalid",
    "BucketError",
    "BucketRequiresPerson",
    "BucketUnreadable",
    "agent_bucket_key",
    "cap_for",
    "check",
    "list_buckets",
    "set_cap",
    "state_for",
    "state_from_row",
    "view",
]
