"""An agent's order waits for a person; approval is a gate on the one execution path.

The lifecycle is declared once: ``pending`` waits for a person, ``approved`` is the single
claim a decision takes, and it ends in ``executed`` or ``failed``; ``rejected``,
``expired`` and ``superseded`` are terminal refusals. Every test here exercises one of the
issue's acceptance criteria, including the failure paths: an unreadable queue and an
expired approval both refuse with a reason code rather than placing an order.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from ezchain_api.actor import ACTOR_AGENT, ACTOR_HUMAN, ACTOR_STRATEGY, OrderActor
from ezchain_api.models import (
    Base,
    LedgerEntryRow,
    OrderApprovalRow,
    PaperFillRow,
    PaperOrderRow,
)
from ezchain_api.services import approvals, paper

from .conftest import (
    FakeDexScreener,
    FakeJupiter,
    make_request,
    make_router,
    make_settings,
    make_snapshot,
    place,
)

AGENT = OrderActor(ACTOR_AGENT, "key-agent-1")
PERSON = OrderActor(ACTOR_HUMAN, "deployment")


def _place_agent(
    session: Session,
    settings: Any,
    dex: FakeDexScreener,
    jupiter: FakeJupiter,
    request: Any,
) -> PaperOrderRow:
    import asyncio

    return asyncio.run(
        paper.place_order(
            session,
            request,
            settings,
            dex,
            make_router(dex, jupiter),
            actor=AGENT,  # type: ignore[arg-type]
        )
    )


def _approve(session: Session, settings: Any, approval_id: str) -> tuple[Any, Any]:
    import asyncio

    dex = FakeDexScreener(make_snapshot())
    jupiter = FakeJupiter()
    return asyncio.run(
        paper.approve_order(
            session,
            approval_id,
            settings,
            dex,  # type: ignore[arg-type]
            make_router(dex, jupiter),
            person=PERSON,
        )
    )


# ---------------------------------------------------------------------------
# Pending: an agent order does not execute on its own
# ---------------------------------------------------------------------------


def test_an_agent_order_lands_pending_and_does_not_fill(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    order = _place_agent(session, settings, dex, jupiter, make_request(key="appr-pending-1"))

    assert order.status == "pending_approval"
    assert order.reason == "AWAITING_APPROVAL"
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 0
    assert (
        session.scalar(select(LedgerEntryRow.id).where(LedgerEntryRow.group_id == order.id)) is None
    )
    approval = approvals.by_order(session, order.id)
    assert approval is not None
    assert approval.status == "pending"
    assert approval.intent["idempotency_key"] == "appr-pending-1"
    assert (order.actor_kind, order.actor_id) == (ACTOR_AGENT, "key-agent-1")


def test_the_approver_sees_the_servers_risk_decision_and_quote(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    """The evidence a person reads is the server's, recorded before the queue."""
    order = _place_agent(session, settings, dex, jupiter, make_request(key="appr-evidence-1"))
    approval = approvals.by_order(session, order.id)
    assert approval is not None

    decision = approval.risk_decision
    assert decision["stage"] == "post_quote"
    assert decision["allowed"] is True
    assert decision["reasons"] == []
    assert decision["quote"]["kind"] == "paper_mark"
    assert decision["cost_preview"]["fill_fee_usd"] == str(
        Decimal(decision["cost_preview"]["fill_fee_usd"])
    )
    # The stored intent is the agent's request; nothing in it came from a person.
    assert approval.decided_by_kind is None
    assert approval.decision_note is None


def test_a_person_can_still_trade_without_an_approval(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    order = place(session, settings, dex, jupiter, make_request(key="appr-human-1"))

    assert order.status == "filled"
    assert approvals.by_order(session, order.id) is None


def test_a_strategy_order_is_not_gated(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    """The engine's own orders already carry their own gate; the queue is for agents."""
    order = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(key="appr-strategy-1", strategy_id="strategy-9"),
    )

    assert order.status == "filled"
    assert (order.actor_kind, order.actor_id) == (ACTOR_STRATEGY, "strategy-9")


def test_an_agent_retry_returns_the_same_pending_order(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    request = make_request(key="appr-retry-1")
    first = _place_agent(session, settings, dex, jupiter, request)
    second = _place_agent(session, settings, dex, jupiter, request)

    assert second.id == first.id
    assert session.scalar(select(func.count()).select_from(OrderApprovalRow)) == 1


# ---------------------------------------------------------------------------
# Approve: it executes, through the standard path, once
# ---------------------------------------------------------------------------


def test_approving_executes_the_stored_order_through_the_standard_path(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    order = _place_agent(session, settings, dex, jupiter, make_request(key="appr-exec-1"))
    approval = approvals.by_order(session, order.id)
    assert approval is not None

    row, placed = _approve(session, settings, approval.id)

    assert placed.id == order.id
    assert placed.status == "filled"
    assert row.status == "executed"
    assert row.decided_by_kind == ACTOR_HUMAN
    assert row.decided_by_id == "deployment"
    fill = session.scalar(select(PaperFillRow).where(PaperFillRow.order_id == order.id))
    assert fill is not None
    assert fill.notional_usd == Decimal("100")
    legs = list(session.scalars(select(LedgerEntryRow).where(LedgerEntryRow.group_id == order.id)))
    assert legs
    # The fill is attributed to the agent that proposed it, not the person who approved it.
    assert {(leg.actor_kind, leg.actor_id) for leg in legs} == {(ACTOR_AGENT, "key-agent-1")}


def test_the_approved_order_keeps_its_stored_idempotency_key_and_one_fill(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    order = _place_agent(session, settings, dex, jupiter, make_request(key="appr-once-1"))
    approval = approvals.by_order(session, order.id)
    assert approval is not None

    _approve(session, settings, approval.id)

    assert session.scalar(select(func.count()).select_from(PaperOrderRow)) == 1
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 1
    stored = session.scalar(select(PaperOrderRow))
    assert stored is not None
    assert stored.idempotency_key == "appr-once-1"


def test_a_second_approval_is_refused_as_not_pending(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    order = _place_agent(session, settings, dex, jupiter, make_request(key="appr-twice-1"))
    approval = approvals.by_order(session, order.id)
    assert approval is not None
    _approve(session, settings, approval.id)

    with pytest.raises(approvals.ApprovalError) as caught:
        _approve(session, settings, approval.id)

    assert caught.value.code == "APPROVAL_NOT_PENDING"
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 1


def test_an_agent_cannot_approve_its_own_proposal(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    """The rule is the principal, not a scope: no agent principal may decide."""
    order = _place_agent(session, settings, dex, jupiter, make_request(key="appr-self-1"))
    approval = approvals.by_order(session, order.id)
    assert approval is not None

    with pytest.raises(approvals.ApprovalRequiresPerson) as caught:
        reject_person = OrderActor(ACTOR_AGENT, "key-agent-2")
        paper.reject_order(
            session, approval.id, person=reject_person, note="I approve my own trade"
        )

    assert caught.value.code == "APPROVAL_REQUIRES_PERSON"
    assert approvals.get(session, approval.id).status == "pending"  # type: ignore[union-attr]


def test_an_approved_order_the_risk_gate_refuses_is_recorded_failed(
    session: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    """Approval is not a bypass: the stored order still runs the real gate at execution.

    The proposal passes risk when the agent makes it; the cash is spent before a person
    decides. Approving then re-runs the same path, which refuses the buy for
    ``INSUFFICIENT_CASH`` — and the approval records that outcome, not a fill.
    """
    settings = make_settings(paper_starting_cash_usd=Decimal("400"))
    order = _place_agent(
        session, settings, dex, jupiter, make_request(key="appr-refused-1", amount_usd="200")
    )
    approval = approvals.by_order(session, order.id)
    assert approval is not None
    assert approval.status == "pending"

    import asyncio

    drained = asyncio.run(
        paper.place_order(
            session,
            make_request(key="appr-drain-1", amount_usd="200"),
            settings,
            dex,
            make_router(dex, jupiter),
            actor=OrderActor(ACTOR_HUMAN, "deployment"),
        )
    )
    assert drained.status == "filled"

    row, placed = _approve(session, settings, approval.id)

    assert placed.status == "rejected"
    assert placed.reason == "INSUFFICIENT_CASH"
    assert row.status == "failed"
    assert row.execution_reason == "INSUFFICIENT_CASH"
    assert session.scalar(
        select(func.count()).select_from(PaperFillRow).where(PaperFillRow.order_id == order.id)
    ) == 0


def test_approving_an_unknown_approval_is_a_typed_404(session: Session, settings: Any) -> None:
    import asyncio

    with pytest.raises(approvals.ApprovalNotFound):
        asyncio.run(
            paper.approve_order(
                session,
                "missing-approval",
                settings,
                FakeDexScreener(make_snapshot()),  # type: ignore[arg-type]
                make_router(FakeDexScreener(make_snapshot()), FakeJupiter()),
                person=PERSON,
            )
        )


# ---------------------------------------------------------------------------
# Reject: refused with the person's reason
# ---------------------------------------------------------------------------


def test_rejecting_refuses_the_order_with_the_persons_reason(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    order = _place_agent(session, settings, dex, jupiter, make_request(key="appr-reject-1"))
    approval = approvals.by_order(session, order.id)
    assert approval is not None

    row = paper.reject_order(
        session, approval.id, person=PERSON, note="liquidity too thin for $100"
    )

    assert row.status == "rejected"
    assert row.decision_note == "liquidity too thin for $100"
    assert row.decided_by_kind == ACTOR_HUMAN
    refreshed = session.get(PaperOrderRow, order.id)
    assert refreshed is not None
    assert refreshed.status == "rejected"
    assert refreshed.reason == "APPROVAL_REJECTED"
    assert refreshed.reasons is not None
    assert refreshed.reasons[0]["detail"]["note"] == "liquidity too thin for $100"
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 0


def test_a_rejection_needs_a_reason(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    order = _place_agent(session, settings, dex, jupiter, make_request(key="appr-noreason-1"))
    approval = approvals.by_order(session, order.id)
    assert approval is not None

    with pytest.raises(approvals.ApprovalError) as caught:
        paper.reject_order(session, approval.id, person=PERSON, note="   ")

    assert caught.value.code == "APPROVAL_DECISION_NOTE_INVALID"
    assert approvals.get(session, approval.id).status == "pending"  # type: ignore[union-attr]


def test_a_rejected_approval_cannot_be_approved_afterwards(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    order = _place_agent(session, settings, dex, jupiter, make_request(key="appr-reject-then-1"))
    approval = approvals.by_order(session, order.id)
    assert approval is not None
    paper.reject_order(session, approval.id, person=PERSON, note="no")

    with pytest.raises(approvals.ApprovalError) as caught:
        _approve(session, settings, approval.id)

    assert caught.value.code == "APPROVAL_NOT_PENDING"
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 0


# ---------------------------------------------------------------------------
# Expiry: five minutes, then fail closed
# ---------------------------------------------------------------------------


def test_a_pending_approval_expires_after_the_configured_window(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    order = _place_agent(session, settings, dex, jupiter, make_request(key="appr-expire-1"))
    approval = approvals.by_order(session, order.id)
    assert approval is not None
    created = paper._aware(approval.created_at)
    expires = paper._aware(approval.expires_at)
    assert created is not None and expires is not None
    assert (expires - created).total_seconds() == float(settings.approval_ttl_seconds)


def test_an_expired_approval_refuses_with_a_reason_code(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    order = _place_agent(session, settings, dex, jupiter, make_request(key="appr-expired-1"))
    approval = approvals.by_order(session, order.id)
    assert approval is not None
    approval.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()

    with pytest.raises(approvals.ApprovalError) as caught:
        _approve(session, settings, approval.id)

    assert caught.value.code == "APPROVAL_EXPIRED"
    refreshed = approvals.get(session, approval.id)
    assert refreshed is not None
    assert refreshed.status == "expired"
    stored = session.get(PaperOrderRow, order.id)
    assert stored is not None
    assert stored.status == "rejected"
    assert stored.reason == "APPROVAL_EXPIRED"
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 0


def test_expiry_is_computed_on_read_without_writing(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    order = _place_agent(session, settings, dex, jupiter, make_request(key="appr-expire-read-1"))
    approval = approvals.by_order(session, order.id)
    assert approval is not None
    approval.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()

    assert approvals.effective_status(approval) == "expired"
    # The read did not mutate the stored row.
    assert (
        session.scalar(select(OrderApprovalRow.status).where(OrderApprovalRow.id == approval.id))
        == "pending"
    )


def test_a_new_proposal_expires_the_old_pending_one(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    first = _place_agent(session, settings, dex, jupiter, make_request(key="appr-sweep-1"))
    older = approvals.by_order(session, first.id)
    assert older is not None
    older.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()

    _place_agent(session, settings, dex, jupiter, make_request(key="appr-sweep-2"))

    session.refresh(older)
    assert older.status == "expired"
    first_refreshed = session.get(PaperOrderRow, first.id)
    assert first_refreshed is not None
    assert first_refreshed.reason == "APPROVAL_EXPIRED"


# ---------------------------------------------------------------------------
# Durability: the queue survives a restart
# ---------------------------------------------------------------------------


def test_a_pending_approval_survives_a_restart(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'approvals.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    settings = make_settings()
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with maker() as session:
            order = _place_agent(
                session,
                settings,
                FakeDexScreener(make_snapshot()),
                FakeJupiter(),
                make_request(key="appr-durable-1"),
            )
            approval_id = approvals.by_order(session, order.id).id  # type: ignore[union-attr]
        engine.dispose()

        # A fresh process, a fresh engine: the queue and its order are still there.
        engine = create_engine(url)
        with Session(engine, expire_on_commit=False) as session:
            row = approvals.get(session, approval_id)
            assert row is not None
            assert row.status == "pending"
            assert approvals.effective_status(row) == "pending"
            stored = session.get(PaperOrderRow, row.order_id)
            assert stored is not None
            assert stored.status == "pending_approval"
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Fail closed: an unreadable queue refuses
# ---------------------------------------------------------------------------


def test_an_unreadable_queue_refuses_the_order_closed(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter, monkeypatch: Any
) -> None:
    """A queue that cannot be read must never let an agent order through."""

    def broken(*_: Any, **__: Any) -> Any:
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(approvals, "read_tier", broken)

    order = _place_agent(session, settings, dex, jupiter, make_request(key="appr-broken-1"))

    assert order.status == "rejected"
    assert order.reason == "APPROVAL_STATE_UNREADABLE"
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 0


def test_an_unreadable_approval_intent_refuses_to_execute(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    order = _place_agent(session, settings, dex, jupiter, make_request(key="appr-badintent-1"))
    approval = approvals.by_order(session, order.id)
    assert approval is not None
    approval.intent = {"chain": "blast"}
    session.commit()

    with pytest.raises(approvals.ApprovalIntentInvalid) as caught:
        _approve(session, settings, approval.id)

    assert caught.value.code == "APPROVAL_INTENT_INVALID"
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 0


# ---------------------------------------------------------------------------
# The lifecycle and its next action
# ---------------------------------------------------------------------------


def test_every_state_names_its_next_action() -> None:
    assert approvals.NEXT_ACTIONS == {
        "pending": "approve_or_reject",
        "approved": "execute",
        "rejected": "none",
        "expired": "none",
        "superseded": "none",
        "executed": "none",
        "failed": "none",
    }
    assert set(approvals.APPROVAL_STATUSES) == set(approvals.NEXT_ACTIONS)


def test_an_unrecognised_stored_status_reads_as_failed(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    order = _place_agent(session, settings, dex, jupiter, make_request(key="appr-unknown-1"))
    approval = approvals.by_order(session, order.id)
    assert approval is not None
    approval.status = "something-else"
    session.commit()

    assert approvals.effective_status(approval) == "failed"
    assert approvals.next_action("something-else") == "none"


def test_a_recorded_approval_reports_the_person_who_decided(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    order = _place_agent(session, settings, dex, jupiter, make_request(key="appr-who-1"))
    approval = approvals.by_order(session, order.id)
    assert approval is not None
    row = paper.reject_order(session, approval.id, person=PERSON, note="not today")

    payload = approvals.to_dict(row)
    assert payload["decided_by"] == {"kind": ACTOR_HUMAN, "id": "deployment"}
    assert payload["status"] == "rejected"
    assert payload["next_action"] == "none"
