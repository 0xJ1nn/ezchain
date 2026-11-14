"""An agent's bucket: a cap on net cash deployed, measured on the ledger.

The bucket is not a balance. What an agent has deployed is replayed from the
``cash:USD`` legs attributed to its ``bucket_id``, so these tests assert against the
journal and the replayed figure, never against a stored number. The gate is entry-only:
a cap refuses a buy that would take the deployment past it, and never refuses an exit.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ezchain_api.actor import ACTOR_AGENT, ACTOR_HUMAN, OrderActor
from ezchain_api.models import AgentBucketRow, Base, LedgerEntryRow, OrderApprovalRow, StrategyRow
from ezchain_api.services import approvals, buckets, paper
from ezchain_core.ledger import cash_account

from .conftest import (
    FakeDexScreener,
    FakeJupiter,
    make_request,
    make_router,
    make_settings,
    make_snapshot,
    place,
)

AGENT = OrderActor(ACTOR_AGENT, "key-agent-1", owner="alice")
OTHER_AGENT = OrderActor(ACTOR_AGENT, "key-agent-2", owner="bob")
ALICE = OrderActor(ACTOR_HUMAN, "alice-user", owner="alice")
BOB = OrderActor(ACTOR_HUMAN, "bob-user", owner="bob")


def _place_agent(
    session: Session,
    settings: Any,
    dex: FakeDexScreener,
    jupiter: FakeJupiter,
    request: Any,
    actor: OrderActor = AGENT,
) -> Any:
    return asyncio.run(
        paper.place_order(session, request, settings, dex, make_router(dex, jupiter), actor=actor)
    )


@pytest.fixture
def settings() -> Any:
    """A fee-free paper account, so a cap of N admits exactly N of notional.

    The fill fee is a real cash leg and therefore counts against the bucket; one test
    below proves that. The rest of the suite pins the cap arithmetic without it.
    """
    return make_settings(paper_fee_bps=0)


@pytest.fixture(autouse=True)
def full_auto(session: Session, settings: Any) -> None:
    """These tests are about the money gate, so the approval queue is opted out of."""
    for person in (ALICE, BOB):
        approvals.set_tier(
            session, tier="full_auto", actor=person, settings=settings, acknowledge_risk=True
        )


def _cap(session: Session, actor: OrderActor, amount: str, person: OrderActor = ALICE) -> None:
    buckets.set_cap(session, actor.id or "", Decimal(amount), actor=person)


def _bucket_legs(session: Session, agent_id: str) -> list[LedgerEntryRow]:
    return list(
        session.scalars(
            select(LedgerEntryRow)
            .where(LedgerEntryRow.bucket_id == agent_id)
            .order_by(LedgerEntryRow.seq)
        )
    )


def _deployed(session: Session, agent_id: str) -> Decimal:
    """The deployment as the replay reports it: negated attributed cash."""
    replay = paper.project_ledger(paper.ledger_rows(session))
    return -replay.bucket_cash.get(agent_id, Decimal(0))


# --- the cap is enforced ----------------------------------------------------


def test_a_buy_up_to_the_cap_fills_and_one_past_it_is_refused(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    """Fill to the cap, then refuse: the boundary itself is spendable."""
    _cap(session, AGENT, "100")
    dex.set(make_snapshot(price=Decimal("2"), liquidity=Decimal("500000")))

    fills = [
        _place_agent(session, settings, dex, jupiter, make_request(amount_usd="50", key=f"cap-{i}"))
        for i in range(2)
    ]
    assert [order.status for order in fills] == ["filled", "filled"]
    assert _deployed(session, "key-agent-1") == Decimal("100")

    past = _place_agent(
        session, settings, dex, jupiter, make_request(amount_usd="1", key="cap-past")
    )
    assert past.status == "rejected"
    assert past.reason == "AGENT_BUCKET_EXCEEDED"
    # Nothing was journaled for the refusal, so the deployment is untouched.
    assert _deployed(session, "key-agent-1") == Decimal("100")


def test_the_refusal_names_the_worst_case_in_dollars(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    _cap(session, AGENT, "100")
    dex.set(make_snapshot(price=Decimal("2"), liquidity=Decimal("500000")))
    _place_agent(session, settings, dex, jupiter, make_request(amount_usd="100", key="worst-fill"))

    refused = _place_agent(
        session, settings, dex, jupiter, make_request(amount_usd="25", key="worst-past")
    )

    detail = refused.reasons[0]["detail"]
    assert refused.reasons[0]["code"] == "AGENT_BUCKET_EXCEEDED"
    assert Decimal(detail["cap_usd"]) == Decimal("100")
    assert Decimal(detail["deployed_usd"]) == Decimal("100")
    assert Decimal(detail["available_usd"]) == Decimal("0")
    assert Decimal(detail["requested_usd"]) == Decimal("25")
    assert Decimal(detail["worst_case_usd"]) == Decimal("25")
    # The detail is dollars, not a count or a percentage.
    assert Decimal(detail["worst_case_usd"]) > Decimal(detail["available_usd"])


def test_a_refused_bucket_order_never_reaches_the_quote(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    """The gate is before the market read: the venue source is never asked."""
    _cap(session, AGENT, "10")
    dex.set(make_snapshot(price=Decimal("2"), liquidity=Decimal("500000")))
    _place_agent(session, settings, dex, jupiter, make_request(amount_usd="10", key="pre-fill"))
    before = dex.calls

    refused = _place_agent(
        session, settings, dex, jupiter, make_request(amount_usd="5", key="pre-past")
    )

    assert refused.reason == "AGENT_BUCKET_EXCEEDED"
    assert dex.calls == before
    assert refused.quote_json is None


def test_the_fill_fee_counts_against_the_bucket(
    session: Session, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    """The fee is a real cash leg, so the worst case is notional plus fee.

    At 3% the fee fits inside the notional: a $98 buy costs $100.94, which does not fit a
    $100 cap even though the plain notional does. The pre-quote gate lets it through and
    the fill-window gate refuses it, which is the point of checking the worst case twice.
    """
    settings = make_settings(paper_fee_bps=300)
    approvals.set_tier(
        session, tier="full_auto", actor=ALICE, settings=settings, acknowledge_risk=True
    )
    _cap(session, AGENT, "100")
    dex.set(make_snapshot(price=Decimal("2"), liquidity=Decimal("500000")))

    refused = _place_agent(
        session, settings, dex, jupiter, make_request(amount_usd="98", key="fee-past")
    )
    assert refused.status == "rejected"
    assert refused.reason == "AGENT_BUCKET_EXCEEDED"
    detail = refused.reasons[0]["detail"]
    assert Decimal(detail["worst_case_usd"]) > Decimal(detail["cap_usd"])
    # The refusal happened after the quote, so the cost is on the record.
    assert refused.quote_json is not None

    filled = _place_agent(
        session, settings, dex, jupiter, make_request(amount_usd="90", key="fee-fill")
    )
    assert filled.status == "filled"
    # 90 notional + 2.70 fee: the deployment includes the fee, not only the notional.
    assert _deployed(session, "key-agent-1") == Decimal("92.7")


# --- the cap is per agent, not per workspace --------------------------------


def test_one_agents_spending_does_not_consume_another_agents_cap(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    _cap(session, AGENT, "100")
    _cap(session, OTHER_AGENT, "100", person=BOB)
    dex.set(make_snapshot(price=Decimal("2"), liquidity=Decimal("500000")))

    first = _place_agent(
        session, settings, dex, jupiter, make_request(amount_usd="100", key="iso-a")
    )
    second = _place_agent(
        session,
        settings,
        dex,
        jupiter,
        make_request(amount_usd="100", key="iso-b"),
        actor=OTHER_AGENT,
    )

    assert first.status == "filled"
    assert second.status == "filled"
    assert _deployed(session, "key-agent-1") == Decimal("100")
    assert _deployed(session, "key-agent-2") == Decimal("100")


def test_an_uncapped_agent_is_governed_by_the_account_gates_alone(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    """No cap is not a zero cap: an agent whose owner set none keeps the old behaviour."""
    dex.set(make_snapshot(price=Decimal("2"), liquidity=Decimal("500000")))

    order = _place_agent(
        session, settings, dex, jupiter, make_request(amount_usd="100", key="uncapped")
    )

    assert order.status == "filled"
    assert buckets.cap_for(session, "key-agent-1") is None


def test_a_persons_order_is_never_charged_to_a_bucket(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    dex.set(make_snapshot(price=Decimal("2"), liquidity=Decimal("500000")))
    order = asyncio.run(
        paper.place_order(
            session,
            make_request(amount_usd="100", key="human-bucket"),
            settings,
            dex,
            make_router(dex, jupiter),
            actor=ALICE,
        )
    )

    assert order.status == "filled"
    assert all(row.bucket_id is None for row in paper.ledger_rows(session)[2:])


# --- the ledger is the only accountant --------------------------------------


def test_the_bucket_is_ledger_entries_and_has_no_balance_column(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    _cap(session, AGENT, "500")
    dex.set(make_snapshot(price=Decimal("2"), liquidity=Decimal("500000")))
    _place_agent(session, settings, dex, jupiter, make_request(amount_usd="100", key="ledger-1"))

    # The only stored bucket state is the cap; nothing caches a deployment.
    columns = set(AgentBucketRow.__table__.columns.keys())  # type: ignore[attr-defined]
    assert columns == {
        "agent_id",
        "owner",
        "cap_usd",
        "updated_at",
        "updated_by_kind",
        "updated_by_id",
    }
    legs = _bucket_legs(session, "key-agent-1")
    assert legs, "the journal must attribute the agent's cash legs"
    assert all(row.account == cash_account() for row in legs)
    assert sum(row.usd_value for row in legs) == Decimal("-100")


def test_an_exit_returns_cash_to_the_bucket_and_funds_the_next_entry(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    _cap(session, AGENT, "100")
    dex.set(make_snapshot(price=Decimal("2"), liquidity=Decimal("500000")))
    _place_agent(session, settings, dex, jupiter, make_request(amount_usd="100", key="rt-buy"))
    assert _deployed(session, "key-agent-1") == Decimal("100")

    dex.set(make_snapshot(price=Decimal("4"), liquidity=Decimal("500000")))
    sold = _place_agent(
        session,
        settings,
        dex,
        jupiter,
        make_request(side="sell", amount_usd="50", reduce_only=True, key="rt-sell"),
    )
    assert sold.status == "filled"
    # The exit returned cash, so the deployment fell by the proceeds.
    assert _deployed(session, "key-agent-1") < Decimal("100")

    # Room the exit freed is spendable again without the owner raising the cap.
    again = _place_agent(
        session, settings, dex, jupiter, make_request(amount_usd="10", key="rt-again")
    )
    assert again.status == "filled"


# --- the bucket cannot be escaped -------------------------------------------


def test_a_strategy_an_agent_created_draws_from_that_agents_bucket(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    """Wrapping the automation in a strategy does not escape the cap.

    A strategy order is attributed to the strategy, so the bucket is resolved through the
    principal that created it. An agent that creates a strategy spends the same money.
    """
    session.add(
        StrategyRow(
            id="strategy-agent-owned",
            name="agent dca",
            chain="blast",
            token_address="0x0000000000000000000000000000000000000abc",
            token_symbol="TEST",
            kind="dca",
            params={"order_usd": 100, "interval_minutes": 60},
            status="active",
            created_by_kind=ACTOR_AGENT,
            created_by_id="key-agent-1",
            created_by_owner="alice",
        )
    )
    session.commit()
    _cap(session, AGENT, "30")
    dex.set(make_snapshot(price=Decimal("2"), liquidity=Decimal("500000")))

    order = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(amount_usd="100", key="strat-bucket", strategy_id="strategy-agent-owned"),
    )

    assert order.status == "rejected"
    assert order.reason == "AGENT_BUCKET_EXCEEDED"
    assert _deployed(session, "key-agent-1") == Decimal("0")


def test_a_strategy_a_person_created_draws_from_no_bucket(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    session.add(
        StrategyRow(
            id="strategy-human-owned",
            name="person dca",
            chain="blast",
            token_address="0x0000000000000000000000000000000000000abc",
            token_symbol="TEST",
            kind="dca",
            params={"order_usd": 100, "interval_minutes": 60},
            status="active",
            created_by_kind=ACTOR_HUMAN,
            created_by_id="deployment",
            created_by_owner="deployment",
        )
    )
    session.commit()
    _cap(session, AGENT, "0")
    dex.set(make_snapshot(price=Decimal("2"), liquidity=Decimal("500000")))

    order = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(amount_usd="100", key="strat-human", strategy_id="strategy-human-owned"),
    )

    assert order.status == "filled"
    assert all(row.bucket_id is None for row in paper.ledger_rows(session)[2:])


def test_an_approval_cannot_outrun_the_cap(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    """A person approving a queued agent order does not grant more room than the cap.

    The proposal is made while the bucket has room; the owner then lowers the cap; the
    approval re-enters ``place_order`` and the bucket gate refuses it there.
    """
    from ezchain_api.actor import ACTOR_HUMAN as HUMAN

    # Back to manual, so the agent's order waits for a person.
    approvals.set_tier(session, tier="manual", actor=ALICE, settings=settings)
    _cap(session, AGENT, "120")
    dex.set(make_snapshot(price=Decimal("2"), liquidity=Decimal("500000")))

    proposed = _place_agent(
        session, settings, dex, jupiter, make_request(amount_usd="100", key="appr-bucket-1")
    )
    assert proposed.status == "pending_approval"
    assert proposed.reason == "AWAITING_APPROVAL"

    # The owner tightens the boundary after the proposal was queued.
    _cap(session, AGENT, "10")

    person = OrderActor(HUMAN, "alice-user", owner="alice")
    row = session.scalar(select(OrderApprovalRow).where(OrderApprovalRow.order_id == proposed.id))
    assert row is not None
    _, executed = asyncio.run(
        paper.approve_order(
            session, row.id, settings, dex, make_router(dex, jupiter), person=person
        )
    )

    assert executed.status == "rejected"
    assert executed.reason == "AGENT_BUCKET_EXCEEDED"
    assert _deployed(session, "key-agent-1") == Decimal("0")


# --- restart durability -----------------------------------------------------


def test_the_cap_and_the_deployment_survive_a_restart(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'bucket.db'}"
    settings = make_settings(paper_fee_bps=0)
    dex = FakeDexScreener(make_snapshot(price=Decimal("2"), liquidity=Decimal("500000")))
    jupiter = FakeJupiter()

    first = create_engine(url)
    Base.metadata.create_all(first)
    with Session(first, expire_on_commit=False) as session:
        approvals.set_tier(
            session,
            tier="full_auto",
            actor=ALICE,
            settings=settings,
            acknowledge_risk=True,
        )
        _cap(session, AGENT, "40")
        assert (
            _place_agent(
                session, settings, dex, jupiter, make_request(amount_usd="40", key="rs-1")
            ).status
            == "filled"
        )
    first.dispose()

    second = create_engine(url)
    with Session(second, expire_on_commit=False) as session:
        # A fresh process re-reads the cap from its row and the deployment from the journal.
        assert buckets.cap_for(session, "key-agent-1") == Decimal("40")
        assert _deployed(session, "key-agent-1") == Decimal("40")
        refused = _place_agent(
            session, settings, dex, jupiter, make_request(amount_usd="1", key="rs-2")
        )
        assert refused.reason == "AGENT_BUCKET_EXCEEDED"
    second.dispose()


# --- cap change semantics: never traps an exit ------------------------------


def test_lowering_the_cap_below_the_deployment_refuses_new_orders_but_not_exits(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    """The reduce-only rule: a lowered cap stops entries and never traps a position."""
    _cap(session, AGENT, "200")
    dex.set(make_snapshot(price=Decimal("2"), liquidity=Decimal("500000")))
    _place_agent(session, settings, dex, jupiter, make_request(amount_usd="200", key="low-buy"))

    # The owner lowers the cap under what is already deployed.
    _cap(session, AGENT, "50")
    assert _deployed(session, "key-agent-1") == Decimal("200")

    blocked = _place_agent(
        session, settings, dex, jupiter, make_request(amount_usd="10", key="low-past")
    )
    assert blocked.status == "rejected"
    assert blocked.reason == "AGENT_BUCKET_EXCEEDED"

    # Every exit still settles, which is what brings the deployment back inside the cap.
    dex.set(make_snapshot(price=Decimal("3"), liquidity=Decimal("500000")))
    exit_order = _place_agent(
        session,
        settings,
        dex,
        jupiter,
        make_request(side="sell", amount_usd="200", reduce_only=True, key="low-exit"),
    )
    assert exit_order.status == "filled"
    assert _deployed(session, "key-agent-1") < Decimal("50")


def test_a_cap_of_zero_refuses_every_entry_and_still_allows_an_exit(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    _cap(session, AGENT, "100")
    dex.set(make_snapshot(price=Decimal("2"), liquidity=Decimal("500000")))
    _place_agent(session, settings, dex, jupiter, make_request(amount_usd="100", key="zero-buy"))

    _cap(session, AGENT, "0")
    blocked = _place_agent(
        session, settings, dex, jupiter, make_request(amount_usd="1", key="zero-past")
    )
    assert blocked.reason == "AGENT_BUCKET_EXCEEDED"

    dex.set(make_snapshot(price=Decimal("3"), liquidity=Decimal("500000")))
    exit_order = _place_agent(
        session,
        settings,
        dex,
        jupiter,
        make_request(side="sell", amount_usd="100", reduce_only=True, key="zero-exit"),
    )
    assert exit_order.status == "filled"


# --- setting the cap: only the owning person --------------------------------


def test_an_agent_cannot_set_its_own_cap(session: Session) -> None:
    with pytest.raises(buckets.BucketRequiresPerson) as caught:
        buckets.set_cap(session, "key-agent-1", Decimal("1000"), actor=AGENT)
    assert caught.value.code == "AGENT_BUCKET_REQUIRES_PERSON"
    assert session.get(AgentBucketRow, "key-agent-1") is None


def test_another_user_cannot_move_an_agents_cap(session: Session) -> None:
    _cap(session, AGENT, "100")

    with pytest.raises(buckets.BucketRequiresPerson):
        buckets.set_cap(session, "key-agent-1", Decimal("9999"), actor=BOB)
    assert buckets.cap_for(session, "key-agent-1") == Decimal("100")


def test_a_negative_cap_is_refused_and_writes_nothing(session: Session) -> None:
    with pytest.raises(buckets.BucketCapInvalid) as caught:
        buckets.set_cap(session, "key-agent-1", Decimal("-1"), actor=ALICE)
    assert caught.value.code == "AGENT_BUCKET_CAP_INVALID"
    assert session.get(AgentBucketRow, "key-agent-1") is None


def test_raising_a_cap_is_a_single_row_and_keeps_its_provenance(session: Session) -> None:
    _cap(session, AGENT, "100")
    _cap(session, AGENT, "250")

    row = session.get(AgentBucketRow, "key-agent-1")
    assert row is not None
    assert Decimal(str(row.cap_usd)) == Decimal("250")
    assert (row.updated_by_kind, row.updated_by_id) == (ACTOR_HUMAN, "alice-user")
    assert row.owner == "alice"


# --- fail closed ------------------------------------------------------------


def test_an_unreadable_bucket_state_refuses_the_entry(session: Session) -> None:
    """Unknown is never unlimited: a failing read refuses instead of letting the buy through."""
    refusal = buckets.check(session, "key-agent-1", Decimal(0), Decimal("10"), fee_usd=Decimal(0))
    assert refusal is None  # no row = no cap, which is the honest default

    original = Session.get
    try:

        def explode(self: Session, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("journal unavailable")

        Session.get = explode  # type: ignore[method-assign]
        refusal = buckets.check(session, "key-agent-1", Decimal(0), Decimal("10"))
    finally:
        Session.get = original  # type: ignore[method-assign]

    assert refusal is not None
    assert refusal.code == "AGENT_BUCKET_UNREADABLE"
    # The persisted detail names the agent and never the driver's own message.
    assert refusal.detail == {"agent_id": "key-agent-1"}


def test_an_unreadable_bucket_refuses_a_buy_and_still_settles_a_sell(
    session: Session, settings: Any, dex: FakeDexScreener, jupiter: FakeJupiter
) -> None:
    """Fail closed on money never becomes fail-trapped: the exit still settles."""
    _cap(session, AGENT, "100")
    dex.set(make_snapshot(price=Decimal("2"), liquidity=Decimal("500000")))
    _place_agent(
        session, settings, dex, jupiter, make_request(amount_usd="100", key="fail-closed-buy")
    )

    original = Session.get
    try:

        def explode(self: Session, entity: Any, *args: Any, **kwargs: Any) -> Any:
            # Only the bucket read fails; every earlier gate still works, so the refusal
            # this test sees is the bucket's own.
            if entity is AgentBucketRow:
                raise RuntimeError("bucket table unavailable")
            return original(self, entity, *args, **kwargs)

        Session.get = explode  # type: ignore[method-assign]
        blocked = _place_agent(
            session, settings, dex, jupiter, make_request(amount_usd="10", key="fail-closed-buy-2")
        )
        exited = _place_agent(
            session,
            settings,
            dex,
            jupiter,
            make_request(side="sell", amount_usd="100", reduce_only=True, key="fail-closed-exit"),
        )
    finally:
        Session.get = original  # type: ignore[method-assign]

    assert blocked.status == "rejected"
    assert blocked.reason == "AGENT_BUCKET_UNREADABLE"
    assert exited.status == "filled"
