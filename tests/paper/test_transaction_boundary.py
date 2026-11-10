"""The order, fill and journal commit as one transaction, or not at all.

The ledger is the only accountant (CONTRACT I1) and every order goes Intent → RiskDecision
→ Quote → Order → Fill → Journal. That claim is only measurable if a failure between the
fill write and the commit is forced and the database is then read from a second
connection, where only committed rows are visible.

Two cases are measured here. A failure injected after the fill row has been written to
the open transaction and before the commit must leave no order for the idempotency key,
no fill, no journal group beyond genesis, and must leave the key usable for a retry. A
quote that fails before the idempotency claim must leave no order row at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ezchain_adapters import NoRoute
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


class InjectedFailure(RuntimeError):
    """A deliberate failure at the transaction seam, raised exactly once."""


def make_url(tmp_path: Path) -> str:
    return f"sqlite+pysqlite:///{tmp_path / 'paper-tx.db'}"


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


def test_a_failure_after_the_fill_write_commits_nothing(
    db_session: Session, tmp_path: Path
) -> None:
    """A failure once the fill INSERT has run, before COMMIT, leaves no trace."""
    url = make_url(tmp_path)
    settings = make_settings()
    dex = FakeDexScreener(make_snapshot())
    jupiter = FakeJupiter()
    request = make_request(amount_usd="100", key="tx-boundary")

    raised: list[bool] = []

    def fail_after_the_fill_is_written(session: Session, _flush_context: object) -> None:
        if raised:
            return
        if not any(isinstance(obj, PaperFillRow) for obj in session.new):
            return
        # The flush has emitted this transaction's INSERTs but has not committed them,
        # so the fill is visible inside the transaction here and nowhere else yet.
        written = session.connection().execute(text("SELECT count(*) FROM paper_fills")).scalar()
        assert written == 1
        raised.append(True)
        raise InjectedFailure("failure between the fill write and the commit")

    event.listen(db_session, "after_flush", fail_after_the_fill_is_written)

    with pytest.raises(InjectedFailure):
        place(db_session, settings, dex, jupiter, request)

    assert raised == [True], "the injected failure never reached the fill write"
    # What request teardown does with a session whose flush failed.
    db_session.rollback()

    assert committed_counts(url) == {"orders": 0, "fills": 0, "ledger": 2, "snapshots": 0}

    fresh = create_engine(url)
    try:
        with Session(fresh, expire_on_commit=False) as session:
            # No order row may exist for the key whose fill was interrupted.
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(PaperOrderRow)
                    .where(PaperOrderRow.idempotency_key == request.idempotency_key)
                )
                == 0
            )
            groups = set(session.scalars(select(LedgerEntryRow.group_id)))
            assert groups == {paper.GENESIS_GROUP}
            assert session.scalar(select(func.count()).select_from(LedgerEntryRow)) == 2
            # The account still shows only the genesis journal: starting cash, nothing spent.
            assert paper.account_snapshot(session)["cash_usd"] == "10000"
    finally:
        fresh.dispose()

    # The failure claimed no key and left the session usable: the same request retries and
    # fills on the same session, and the listener that raised once stays silent.
    retried = place(db_session, settings, dex, jupiter, request)
    assert retried.status == "filled"

    assert committed_counts(url) == {"orders": 1, "fills": 1, "ledger": 6, "snapshots": 1}


def test_a_quote_failure_before_the_claim_leaves_no_order_row(
    db_session: Session, tmp_path: Path
) -> None:
    """A quote refused before the key is claimed persists no order, fill or journal."""
    url = make_url(tmp_path)
    settings = make_settings()
    # An unpriced snapshot makes the paper-mark rung refuse the quote (NoRoute).
    dex = FakeDexScreener(make_snapshot(price=None))
    jupiter = FakeJupiter()
    request = make_request(amount_usd="100", key="no-quote")

    with pytest.raises(NoRoute):
        place(db_session, settings, dex, jupiter, request)

    assert committed_counts(url) == {"orders": 0, "fills": 0, "ledger": 2, "snapshots": 0}

    # The key was never claimed, so a priced retry is a first order, not a replay.
    dex.set(make_snapshot(price=Decimal("2000")))
    order = place(db_session, settings, dex, jupiter, request)

    assert order.status == "filled"
    assert committed_counts(url)["orders"] == 1
