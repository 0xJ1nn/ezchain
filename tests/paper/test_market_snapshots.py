"""Market snapshot writes: one row per token, newest observation wins, no write races.

Two independent writers share the ``(chain, token_address)`` key: an order prices against a
live snapshot and persists it, and the market monitor refreshes marks on its own schedule.
These tests pin the atomic upsert that makes the two converge, and the ordering rule that
keeps a delayed or clock-skewed observation from replacing a good mark.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy import Engine, create_engine, event, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from ezchain_api.models import Base, MarketSnapshotRow
from ezchain_api.services import market_snapshots

from .conftest import (
    FakeDexScreener,
    FakeJupiter,
    make_request,
    make_settings,
    make_snapshot,
    place,
)


def _now_minus(seconds: float) -> datetime:
    return datetime.now(UTC) - timedelta(seconds=seconds)


def _aware(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _file_engine(path: Path) -> Engine:
    engine = create_engine(
        f"sqlite+pysqlite:///{path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def postgres_database() -> Iterator[str]:
    """A throwaway Postgres database carrying the snapshot table and its unique index."""
    base = os.environ.get("EZCHAIN_TEST_DATABASE_URL", "")
    if not base.startswith("postgresql"):
        pytest.skip("EZCHAIN_TEST_DATABASE_URL is not a Postgres test database")
    parsed = make_url(base)
    name = f"ezchain_snapshot_{uuid4().hex[:8]}_test"
    admin = parsed.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = parsed.set(database=name).render_as_string(hide_password=False)
    engine = create_engine(url)
    try:
        MarketSnapshotRow.__table__.create(engine)
        yield url
    finally:
        engine.dispose()
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _one_row(maker: sessionmaker[Session]) -> MarketSnapshotRow:
    with maker() as session:
        rows = list(session.scalars(select(MarketSnapshotRow)))
    assert len(rows) == 1, rows
    return rows[0]


def test_newer_observation_replaces_the_stored_mark(session: Session) -> None:
    informed = market_snapshots.record_snapshot(
        session, make_snapshot(observed_at=_now_minus(60), price=Decimal("1900"))
    )
    session.commit()
    row_id = informed.id

    newer = market_snapshots.record_snapshot(
        session, make_snapshot(observed_at=_now_minus(1), price=Decimal("2000"))
    )
    session.commit()

    stored = session.scalar(select(MarketSnapshotRow))
    assert stored is not None
    assert stored.id == row_id
    assert stored.price_usd == Decimal("2000")
    assert _aware(stored.observed_at) == _aware(newer.observed_at)


def test_older_observation_does_not_replace_the_stored_mark(session: Session) -> None:
    current = _now_minus(1)
    market_snapshots.record_snapshot(
        session, make_snapshot(observed_at=current, price=Decimal("2000"))
    )
    session.commit()

    returned = market_snapshots.record_snapshot(
        session, make_snapshot(observed_at=_now_minus(60), price=Decimal("1"))
    )
    session.commit()

    stored = session.scalar(select(MarketSnapshotRow))
    assert stored is not None
    assert stored.price_usd == Decimal("2000")
    assert _aware(stored.observed_at) == _aware(current)
    # The caller gets the row that won, not the observation that was dropped.
    assert returned.price_usd == Decimal("2000")


def test_future_dated_observation_does_not_replace_the_stored_mark(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    current = _now_minus(1)
    market_snapshots.record_snapshot(
        session, make_snapshot(observed_at=current, price=Decimal("2000"))
    )
    session.commit()

    ahead = datetime.now(UTC) + timedelta(hours=1)
    returned = market_snapshots.record_snapshot(
        session, make_snapshot(observed_at=ahead, price=Decimal("9999"))
    )
    session.commit()

    assert returned is None
    assert "MARK_TIMESTAMP_FUTURE" in caplog.text
    stored = session.scalar(select(MarketSnapshotRow))
    assert stored is not None
    assert stored.price_usd == Decimal("2000")
    assert _aware(stored.observed_at) == _aware(current)
    # The receiver's clock is stored apart from the source's timestamp.
    assert stored.received_at is not None
    assert _aware(stored.received_at) >= _aware(current)


def test_future_dated_first_observation_is_rejected_and_the_next_correct_one_wins(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    ahead = datetime.now(UTC) + timedelta(hours=1)
    rejected = market_snapshots.record_snapshot(
        session, make_snapshot(observed_at=ahead, price=Decimal("9999"))
    )
    session.commit()

    assert rejected is None
    assert "MARK_TIMESTAMP_FUTURE" in caplog.text
    assert session.scalar(select(MarketSnapshotRow)) is None

    # The rejected report cannot suppress a later correct observation: it is the mark.
    stored = market_snapshots.record_snapshot(
        session, make_snapshot(observed_at=datetime.now(UTC), price=Decimal("2001"))
    )
    session.commit()
    assert stored is not None
    assert stored.price_usd == Decimal("2001")
    assert _aware(stored.received_at) >= _aware(stored.observed_at)


def test_corrected_observation_replaces_a_legacy_far_future_row(session: Session) -> None:
    """A row already frozen in the future must not reject every later correct write.

    The ordering rule bounds the incoming observation by real time, not by the stored
    timestamp alone: a bogus far-future ``observed_at`` cannot outrank a correct one
    forever, or the frozen mark would be permanent.
    """
    frozen = datetime.now(UTC) + timedelta(days=365)
    market_snapshots.record_snapshot(
        session, make_snapshot(observed_at=_now_minus(1), price=Decimal("2000"))
    )
    session.commit()
    session.execute(update(MarketSnapshotRow).values(observed_at=frozen))
    session.commit()

    corrected = market_snapshots.record_snapshot(
        session, make_snapshot(observed_at=datetime.now(UTC), price=Decimal("2001"))
    )
    session.commit()

    assert corrected is not None
    assert corrected.price_usd == Decimal("2001")
    stored = session.scalar(select(MarketSnapshotRow))
    assert stored is not None
    assert stored.price_usd == Decimal("2001")
    assert _aware(stored.observed_at) <= datetime.now(UTC)


def test_received_at_is_the_write_clock_not_the_source_timestamp(session: Session) -> None:
    observed = _now_minus(30)
    before = datetime.now(UTC)
    market_snapshots.record_snapshot(
        session, make_snapshot(observed_at=observed, price=Decimal("2000"))
    )
    session.commit()

    stored = session.scalar(select(MarketSnapshotRow))
    assert stored is not None
    assert _aware(stored.observed_at) == _aware(observed)
    assert stored.received_at is not None
    assert _aware(stored.received_at) >= before - timedelta(seconds=1)
    assert _aware(stored.received_at) > _aware(stored.observed_at)


def test_concurrent_writers_at_one_key_leave_one_row_and_no_error(tmp_path: Path) -> None:
    engine = _file_engine(tmp_path / "snapshot-race.db")
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    older = _now_minus(30)
    newer = _now_minus(1)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def write(observed: datetime, price: str) -> None:
        try:
            with maker() as session:
                barrier.wait(timeout=10)
                market_snapshots.record_snapshot(
                    session, make_snapshot(observed_at=observed, price=Decimal(price))
                )
                session.commit()
        except BaseException as exc:  # noqa: BLE001 - a thread failure is the test failing
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda pair: write(*pair), [(newer, "2000"), (older, "1")]))

    assert not errors, errors
    row = _one_row(maker)
    # The newer observation wins whichever writer reached the key first.
    assert row.price_usd == Decimal("2000")
    assert _aware(row.observed_at) == _aware(newer)
    engine.dispose()


def test_order_racing_a_mark_refresh_still_fills(tmp_path: Path) -> None:
    engine = _file_engine(tmp_path / "order-mark-race.db")
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    settings = make_settings()
    observed = _now_minus(1)
    dex = FakeDexScreener(make_snapshot(observed_at=observed, price=Decimal("2000")))
    jupiter = FakeJupiter(price=Decimal("2000"))
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []
    statuses: list[str] = []

    def order() -> None:
        try:
            with maker() as session:
                barrier.wait(timeout=10)
                placed = place(
                    session,
                    settings,
                    dex,
                    jupiter,
                    make_request(amount_usd="100", key="race-order"),
                )
                statuses.append(placed.status)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def refresh() -> None:
        try:
            with maker() as session:
                barrier.wait(timeout=10)
                market_snapshots.record_snapshot(
                    session, make_snapshot(observed_at=observed, price=Decimal("2000"))
                )
                session.commit()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda fn: fn(), [order, refresh]))

    assert not errors, errors
    assert statuses == ["filled"]
    row = _one_row(maker)
    assert row.price_usd == Decimal("2000")
    engine.dispose()


def test_write_is_one_statement_per_row_without_a_pre_select(tmp_path: Path) -> None:
    engine = _file_engine(tmp_path / "snapshot-queries.db")
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        if "market_snapshots" in statement:
            statements.append(statement)

    tokens = [f"0x{index:040x}" for index in range(6)]
    with maker() as session:
        for index, token in enumerate(tokens):
            market_snapshots.record_snapshot(
                session,
                make_snapshot(address=token, observed_at=_now_minus(10 - index)),
            )
        session.commit()

    inserts = [sql for sql in statements if sql.startswith("INSERT INTO market_snapshots")]
    selects = [
        sql for sql in statements if sql.startswith("SELECT") and "FROM market_snapshots" in sql
    ]
    assert len(inserts) == len(tokens)
    assert len(selects) == len(tokens)
    # No read-then-write: the first statement against the table is the upsert itself.
    assert statements[0].startswith("INSERT INTO market_snapshots")
    engine.dispose()


def test_postgres_upsert_converges_and_keeps_the_newest_mark(postgres_database: str) -> None:
    engine = create_engine(postgres_database)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    older = _now_minus(30)
    newer = _now_minus(1)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def write(observed: datetime, price: str) -> None:
        try:
            with maker() as session:
                barrier.wait(timeout=10)
                market_snapshots.record_snapshot(
                    session, make_snapshot(observed_at=observed, price=Decimal(price))
                )
                session.commit()
        except BaseException as exc:  # noqa: BLE001 - a thread failure is the test failing
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda pair: write(*pair), [(newer, "2000"), (older, "1")]))

    assert not errors, errors
    row = _one_row(maker)
    assert row.price_usd == Decimal("2000")
    assert _aware(row.observed_at) == _aware(newer)

    # A stale poll that arrives after the winner cannot move the mark backwards either.
    with maker() as session:
        market_snapshots.record_snapshot(
            session, make_snapshot(observed_at=_now_minus(120), price=Decimal("7"))
        )
        session.commit()
    assert _one_row(maker).price_usd == Decimal("2000")
    engine.dispose()
