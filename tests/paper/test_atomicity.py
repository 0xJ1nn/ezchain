"""Fills, orders and ledger groups are one write, or they leave nothing behind.

CONTRACT I1 makes the ledger the only accountant and requires the position state and its
journal to commit atomically; I2 requires every order to run Intent → RiskDecision → Quote
→ Order → Fill → Journal. CONTRACT I9 requires the account to be rebuilt from the persisted
journal, not from memory. The claim is measured at every seam between the idempotency claim,
the fill write, the journal write and the commit. A forced failure at any of them must leave
no pending group in the service's own session and no partial rows on a second connection.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ezchain_api.models import (
    Base,
    LedgerEntryRow,
    MarketSnapshotRow,
    PaperFillRow,
    PaperOrderRow,
)
from ezchain_api.services import paper

from .conftest import (
    FakeDexScreener,
    FakeJupiter,
    make_request,
    make_settings,
    make_snapshot,
    place,
)


class InjectedWriteFailure(SQLAlchemyError):
    """A database-class failure at a write seam, using a real SQLAlchemy error type."""


def make_url(tmp_path: Path) -> str:
    return f"sqlite+pysqlite:///{tmp_path / 'paper-atomicity.db'}"


def committed_counts(url: str) -> dict[str, int]:
    """Count rows through a new connection, so only committed writes are visible."""
    engine = create_engine(url)
    try:
        with Session(engine) as session:
            return {
                "orders": session.scalar(select(func.count()).select_from(PaperOrderRow)) or 0,
                "fills": session.scalar(select(func.count()).select_from(PaperFillRow)) or 0,
                "ledger": session.scalar(select(func.count()).select_from(LedgerEntryRow)) or 0,
                "snapshots": session.scalar(select(func.count()).select_from(MarketSnapshotRow))
                or 0,
            }
    finally:
        engine.dispose()


def no_pending_writes(session: Session, key: str) -> None:
    """The service's own session holds no staged group and can be used again right away."""
    assert not any(
        isinstance(obj, (PaperFillRow, LedgerEntryRow, MarketSnapshotRow))
        and getattr(obj, "group_id", None) != paper.GENESIS_GROUP
        for obj in session.new
    )
    assert not session.in_transaction()
    assert (
        session.scalar(
            select(func.count())
            .select_from(PaperOrderRow)
            .where(PaperOrderRow.idempotency_key == key)
        )
        == 0
    )


@pytest.fixture
def db_engine(tmp_path: Path) -> Iterator[Engine]:
    engine = create_engine(make_url(tmp_path))
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Iterator[Session]:
    with Session(db_engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def funded_settings() -> object:
    return make_settings(
        paper_starting_cash_usd=Decimal("50"),
        paper_no_risk=True,
        paper_no_risk_topup_cap_usd=Decimal("1000"),
        paper_mark_max_order_usd=Decimal("1000"),
    )


def test_a_failure_before_the_commit_discards_the_staged_group(
    db_session: Session, tmp_path: Path, funded_settings: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception after the rows are staged but before COMMIT rolls the group back.

    The funding audit runs once, after the fill and both ledger groups have been added to
    the session and before the single commit. If it raises, the service may not return with
    a staged order, fill and journal: a caller that commits afterwards must not be able to
    persist a group with no accounting, or an accounting group with no fill.
    """
    url = make_url(tmp_path)
    dex = FakeDexScreener(make_snapshot())
    jupiter = FakeJupiter()
    request = make_request(amount_usd="100", key="atomicity-staged")

    def fail_after_staging(*args: object, **kwargs: object) -> object:
        raise InjectedWriteFailure("failure after the fill was staged, before the commit")

    monkeypatch.setattr(paper, "_funding_audit", fail_after_staging)

    with pytest.raises(InjectedWriteFailure):
        place(db_session, funded_settings, dex, jupiter, request)  # type: ignore[arg-type]

    no_pending_writes(db_session, request.idempotency_key)
    assert committed_counts(url) == {"orders": 0, "fills": 0, "ledger": 2, "snapshots": 0}

    # The attempt claimed no key, so the same request fills cleanly on a retry.
    monkeypatch.setattr(paper, "_funding_audit", lambda *a, **k: {"trigger": "test"})
    retried = place(db_session, funded_settings, dex, jupiter, request)  # type: ignore[arg-type]
    assert retried.status == "filled"
    assert committed_counts(url)["orders"] == 1
    assert committed_counts(url)["fills"] == 1


def test_a_failure_at_the_ledger_group_write_commits_nothing(
    db_session: Session, tmp_path: Path
) -> None:
    """A failure once the order's ledger group is staged leaves no partial state.

    The fill and the journal legs are emitted in the same flush. Raising after that flush,
    before the commit, must leave no order for the key, no fill, no group beyond genesis and
    the account unchanged when read through a separate connection.
    """
    url = make_url(tmp_path)
    settings = make_settings()
    dex = FakeDexScreener(make_snapshot())
    jupiter = FakeJupiter()
    request = make_request(amount_usd="100", key="atomicity-group")

    raised: list[bool] = []

    def fail_at_the_group_write(session: Session, _flush_context: object) -> None:
        if raised:
            return
        if not any(
            isinstance(obj, LedgerEntryRow) and obj.group_id != paper.GENESIS_GROUP
            for obj in session.new
        ):
            return
        assert session.connection().execute(text("SELECT count(*) FROM paper_fills")).scalar() == 1
        raised.append(True)
        raise InjectedWriteFailure("failure at the ledger group write")

    event.listen(db_session, "after_flush", fail_at_the_group_write)

    with pytest.raises(InjectedWriteFailure):
        place(db_session, settings, dex, jupiter, request)
    assert raised == [True], "the injected failure never reached the ledger group write"

    # No self-rollback: the guard in the service must have rolled the session back, leaving
    # nothing staged for a later commit and leaving the session ready for the retry.
    no_pending_writes(db_session, request.idempotency_key)
    # A second connection sees only committed rows (a complement to the session check).
    assert committed_counts(url) == {"orders": 0, "fills": 0, "ledger": 2, "snapshots": 0}
    with create_engine(url).connect() as connection:
        groups = set(
            connection.execute(text("SELECT DISTINCT group_id FROM ledger_entries")).scalars()
        )
        assert groups == {paper.GENESIS_GROUP}

    event.remove(db_session, "after_flush", fail_at_the_group_write)
    assert place(db_session, settings, dex, jupiter, request).status == "filled"
    assert committed_counts(url)["orders"] == 1


def test_a_failure_in_the_funding_group_commits_no_orphan_group(
    db_session: Session, tmp_path: Path, funded_settings: object
) -> None:
    """A forced failure with a top-up leaves neither the funding group nor the fill group."""
    url = make_url(tmp_path)
    dex = FakeDexScreener(make_snapshot())
    jupiter = FakeJupiter()
    request = make_request(amount_usd="100", key="atomicity-funding")

    raised: list[bool] = []

    def fail_with_both_groups_staged(session: Session, _flush_context: object) -> None:
        if raised or not any(isinstance(obj, PaperFillRow) for obj in session.new):
            return
        groups = {obj.group_id for obj in session.new if isinstance(obj, LedgerEntryRow)}
        assert any(group.startswith("funding:") for group in groups)
        raised.append(True)
        raise InjectedWriteFailure("failure with both ledger groups staged")

    event.listen(db_session, "after_flush", fail_with_both_groups_staged)

    with pytest.raises(InjectedWriteFailure):
        place(db_session, funded_settings, dex, jupiter, request)  # type: ignore[arg-type]
    assert raised == [True], "the injected failure never reached the two-group write"

    # The guard rolls back both staged groups in the service's own session.
    no_pending_writes(db_session, request.idempotency_key)
    with create_engine(url).connect() as connection:
        groups = set(
            connection.execute(text("SELECT DISTINCT group_id FROM ledger_entries")).scalars()
        )
        assert groups == {paper.GENESIS_GROUP}
    assert committed_counts(url) == {"orders": 0, "fills": 0, "ledger": 2, "snapshots": 0}

    event.remove(db_session, "after_flush", fail_with_both_groups_staged)
    order = place(db_session, funded_settings, dex, jupiter, request)  # type: ignore[arg-type]
    assert order.status == "filled"
    with create_engine(url).connect() as connection:
        groups = set(
            connection.execute(text("SELECT DISTINCT group_id FROM ledger_entries")).scalars()
        )
        assert groups == {paper.GENESIS_GROUP, order.id, f"funding:{order.id}"}


def test_a_failure_between_the_claim_and_the_journal_leaves_the_key_reusable(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure after the claim but before the journal rolls the claim back too.

    The idempotency row is claimed and the snapshot staged before the fill legs are written.
    A failure in that earlier window must not leave a pending order with no journal and burn
    the key; the same request must be able to claim and fill cleanly on a retry.
    """
    url = make_url(tmp_path)
    settings = make_settings()
    dex = FakeDexScreener(make_snapshot())
    jupiter = FakeJupiter()
    request = make_request(amount_usd="100", key="atomicity-claim")
    original = paper.evaluate_order
    raised: list[bool] = []

    def fail_once(*args: object, **kwargs: object) -> object:
        if raised:
            return original(*args, **kwargs)  # type: ignore[arg-type]
        raised.append(True)
        raise InjectedWriteFailure("failure between the claim and the journal")

    monkeypatch.setattr(paper, "evaluate_order", fail_once)

    with pytest.raises(InjectedWriteFailure):
        place(db_session, settings, dex, jupiter, request)
    assert raised == [True], "the injected failure never reached the risk gate"

    no_pending_writes(db_session, request.idempotency_key)
    assert committed_counts(url) == {"orders": 0, "fills": 0, "ledger": 2, "snapshots": 0}

    retried = place(db_session, settings, dex, jupiter, request)
    assert retried.status == "filled"
    assert committed_counts(url)["orders"] == 1
    assert committed_counts(url)["fills"] == 1


def test_a_successful_fill_replays_as_one_complete_group(
    db_session: Session, tmp_path: Path
) -> None:
    """A committed group reconstructs from the persisted journal on a fresh engine (I9)."""
    url = make_url(tmp_path)
    settings = make_settings()
    dex = FakeDexScreener(make_snapshot())
    jupiter = FakeJupiter()
    request = make_request(amount_usd="100", key="atomicity-success")

    order = place(db_session, settings, dex, jupiter, request)
    assert order.status == "filled"

    # A fresh engine holds no in-memory ORM state: every number below is rebuilt from rows.
    fresh = create_engine(url)
    try:
        with Session(fresh, expire_on_commit=False) as replay_session:
            fill = replay_session.scalar(
                select(PaperFillRow).where(PaperFillRow.order_id == order.id)
            )
            assert fill is not None
            legs = list(
                replay_session.scalars(
                    select(LedgerEntryRow).where(LedgerEntryRow.group_id == order.id)
                )
            )
            assert len(legs) > 0
            assert sum((leg.usd_value for leg in legs), Decimal(0)) == Decimal(0)
            snapshot = paper.account_snapshot(replay_session)
            notional = Decimal("100")
            fee = notional * Decimal("0.003")
            assert Decimal(snapshot["cash_usd"]) == Decimal("10000") - notional - fee
    finally:
        fresh.dispose()
