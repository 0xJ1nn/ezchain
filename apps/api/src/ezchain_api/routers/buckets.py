"""An agent's capped ledger bucket: read the deployment, set the cap.

An agent order draws only from the cash its owner set aside for that agent. The gate is
in :func:`ezchain_api.services.paper.place_order` and the arithmetic is in
``ezchain_core.bucket``; these routes are the setting surface a person uses to bound an
agent, and the read surface that shows what the ledger says it has deployed.

Only a person sets a cap, and only the agent's own owner may move that boundary. An agent
key may read **its own** bucket and no other: every read is scoped to the authenticated
principal, so one compromised key cannot measure the room its neighbours have left. The
two refusals are deliberately alike — an agent asking for another agent's bucket gets the
same ``404 AGENT_BUCKET_UNKNOWN`` as one asking for an id that was never minted — because
a distinguishable answer would itself be a fact about the other tenant.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ezchain_core import utcnow

from ..actor import ACTOR_AGENT, from_principal
from ..deps import get_session
from ..models import AgentBucketRow
from ..schemas import AgentBucketCapIn, AgentBucketOut, AgentBucketsOut
from ..services import buckets, paper

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


def _refusal(exc: buckets.BucketError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message, "retryable": False},
    )


def _agent_scope(request: Request) -> str | None:
    """The only agent id this caller may read, or ``None`` for a caller who may read all.

    A person (the deployment principal, or a development process with no credential) reads
    every capped agent, which is what the operator surface needs. A scoped agent key reads
    one row: its own, addressed by its key id.
    """
    actor = from_principal(getattr(request.state, "principal", None))
    return actor.id if actor.kind == ACTOR_AGENT else None


def _read(session: Session, row: AgentBucketRow) -> AgentBucketOut:
    """One bucket, with deployment replayed from the journal."""
    replay = paper.project_ledger(paper.ledger_rows(session))
    return AgentBucketOut.model_validate(
        buckets.view(row, replay.bucket_cash.get(row.agent_id, Decimal(0)))
    )


@router.get("/buckets", response_model=AgentBucketsOut)
def list_agent_buckets(
    request: Request, session: Session = Depends(get_session)
) -> AgentBucketsOut:
    """Every agent whose owner set a cap, with the deployment replayed from the ledger.

    An agent key sees its own bucket only. A failure to read the journal is a typed ``409``
    rather than an empty list: an empty list would read as "nothing is capped", which is the
    one answer this surface must not invent. The refusal carries its own message and never
    the driver's, which can name a DSN.
    """
    scoped = _agent_scope(request)
    try:
        replay = paper.project_ledger(paper.ledger_rows(session))
        rows = buckets.list_buckets(session, replay.bucket_cash, agent_id=scoped)
    except Exception as exc:  # noqa: BLE001 - an unreadable bucket state is a typed refusal
        raise _refusal(buckets.BucketUnreadable("the agent buckets could not be read")) from exc
    return AgentBucketsOut(
        as_of=utcnow().isoformat(),
        buckets=[AgentBucketOut.model_validate(row) for row in rows],
    )


@router.get("/buckets/{agent_id}", response_model=AgentBucketOut)
def get_agent_bucket(
    agent_id: str,
    request: Request,
    session: Session = Depends(get_session),
) -> AgentBucketOut:
    """One bucket. Another agent's id and a never-minted id answer identically."""
    scoped: str | None = _agent_scope(request)
    if scoped is not None and agent_id != scoped:
        raise _refusal(buckets.BucketAgentUnknown("this agent has no bucket cap"))
    row = session.get(AgentBucketRow, agent_id)
    if row is None:
        raise _refusal(buckets.BucketAgentUnknown("this agent has no bucket cap"))
    return _read(session, row)


@router.put("/buckets/{agent_id}", response_model=AgentBucketOut)
def set_agent_bucket(
    agent_id: str,
    payload: AgentBucketCapIn,
    request: Request,
    session: Session = Depends(get_session),
) -> AgentBucketOut:
    """Set one agent's cap. Only the agent's owner, and only a person, may."""
    person = from_principal(getattr(request.state, "principal", None))
    try:
        buckets.set_cap(session, agent_id, payload.cap_usd, actor=person)
    except buckets.BucketError as exc:
        raise _refusal(exc) from exc
    row = session.get(AgentBucketRow, agent_id)
    if row is None:  # pragma: no cover - set_cap wrote the row it returns
        raise _refusal(buckets.BucketUnreadable())
    return _read(session, row)
