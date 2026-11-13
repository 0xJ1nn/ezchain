"""Persist normalised market events, read them back, and keep the contract's identity.

The store is the query path; the JSONL log stays the replay input. These tests pin the
rules that make persistence trustworthy rather than merely present:

* one observation is one row, because the schema's key *is* the contract's idempotency
  identity — a retried write cannot duplicate a market number;
* a row reads back as the same ``MarketEvent``, nulls included, so no consumer sees a zero
  where the source said unknown;
* the outbox row commits with the event, and a duplicate produces no second message;
* a failure is reported, not swallowed.

Most cases run on SQLite because that is the database every API test already uses; the
Postgres cases prove the deployment path and are skipped when
``EZCHAIN_TEST_DATABASE_URL`` is unset, exactly as the migration gate is.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from ezchain_api.db import Base
from ezchain_api.models import MarketEventRow, MarketOutboxRow
from ezchain_core import MarketEvent, SourceRef
from ezchain_ingest import (
    MARKET_EVENT_TOPIC,
    MarketStoreError,
    latest_events,
    load_events,
    mark_published,
    pending_outbox,
    record_events,
    record_events_transactionally,
)

BASE = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return BASE + timedelta(seconds=seconds)


def _event(
    *,
    pair: str = "Mint1111111111111111111111111111111111111111",
    source: str = "pumpfun-firehose",
    source_seconds: int = 0,
    ingest_seconds: int = 1,
    price: str | None = "2.5",
    size: str | None = "1.25",
    direction: str | None = "buy",
    confidence: str | None = None,
    units: str = "SOL",
    url: str | None = "https://example.test/firehose",
) -> MarketEvent:
    return MarketEvent(
        chain="solana",
        venue="pumpfun",
        pair=pair,
        price=None if price is None else Decimal(price),
        size=None if size is None else Decimal(size),
        direction=direction,  # type: ignore[arg-type]
        units=units,
        source=SourceRef(source, url, _at(source_seconds)),
        observed_at=_at(ingest_seconds),
        confidence=None if confidence is None else Decimal(confidence),
    )


@pytest.fixture
def store_session(tmp_path: Path) -> Iterator[Session]:
    """A file-backed SQLite database carrying every model, including the market tables."""
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'market.db'}")
    Base.metadata.create_all(engine)
    try:
        with sessionmaker(bind=engine, expire_on_commit=False)() as session:
            yield session
    finally:
        engine.dispose()


def _rows(session: Session, entity: type[object]) -> int:
    return int(session.execute(sa.select(sa.func.count()).select_from(entity)).scalar_one())


def test_a_recorded_event_reads_back_as_the_same_contract_type(store_session: Session) -> None:
    event = _event(confidence="0.75")
    assert record_events(store_session, [event]) == 1
    store_session.commit()

    (stored,) = load_events(store_session)

    assert stored == event
    assert stored.to_dict() == event.to_dict()


def test_unknown_fields_stay_null_and_never_become_zero(store_session: Session) -> None:
    event = _event(price=None, size=None, direction=None, confidence=None)
    record_events(store_session, [event])
    store_session.commit()

    (stored,) = load_events(store_session)

    assert stored.price is None
    assert stored.size is None
    assert stored.direction is None
    assert stored.confidence is None
    assert stored == event


def test_a_repeated_observation_is_one_row_and_one_message(store_session: Session) -> None:
    event = _event()
    assert record_events(store_session, [event]) == 1
    assert record_events(store_session, [event]) == 0
    store_session.commit()

    assert _rows(store_session, MarketEventRow) == 1
    assert _rows(store_session, MarketOutboxRow) == 1


def test_one_batch_stores_every_new_observation(store_session: Session) -> None:
    events = [_event(source_seconds=index) for index in range(5)]
    events.append(events[0])  # a repeat inside the batch is the same observation

    assert record_events(store_session, events) == 5
    store_session.commit()

    assert _rows(store_session, MarketEventRow) == 5


def test_two_sources_observing_the_same_pair_are_two_rows(store_session: Session) -> None:
    """Identity is the source, not the market: two lenses on one pair are two observations."""
    first = _event(source="pumpfun-firehose")
    second = _event(source="jupiter-quote")
    assert record_events(store_session, [first, second]) == 2
    store_session.commit()

    stored = load_events(store_session)
    assert {event.source.name for event in stored} == {"pumpfun-firehose", "jupiter-quote"}


def test_reads_are_ordered_by_source_timestamp_then_identity(store_session: Session) -> None:
    later = _event(source_seconds=20, ingest_seconds=21)
    earlier_b = _event(source="alpha-source", source_seconds=10, ingest_seconds=30)
    earlier_a = _event(source="zeta-source", source_seconds=10, ingest_seconds=31)
    record_events(store_session, [later, earlier_b, earlier_a])
    store_session.commit()

    stored = load_events(store_session)

    assert [event.source.name for event in stored] == [
        "alpha-source",
        "zeta-source",
        "pumpfun-firehose",
    ]


def test_filters_narrow_by_pair_and_time_without_inventing_rows(store_session: Session) -> None:
    old = _event(source_seconds=0, ingest_seconds=1)
    middle = _event(pair="Mint2", source_seconds=60, ingest_seconds=61)
    recent = _event(source_seconds=120, ingest_seconds=121)
    record_events(store_session, [old, middle, recent])
    store_session.commit()

    assert [event.pair for event in load_events(store_session, pair="Mint2")] == ["Mint2"]
    since = load_events(store_session, since=_at(60))
    assert {event.source.observed_at for event in since} == {_at(60), _at(120)}
    bounded = load_events(store_session, since=_at(60), until=_at(120))
    assert [event.source.observed_at for event in bounded] == [_at(60)]


def test_an_empty_store_reads_as_empty_never_as_a_fabricated_row(
    store_session: Session,
) -> None:
    assert load_events(store_session) == ()
    assert latest_events(store_session) == ()
    assert pending_outbox(store_session) == ()


def test_the_outbox_carries_the_recorded_form_without_a_freshness_verdict(
    store_session: Session,
) -> None:
    event = _event(confidence="0.5")
    record_events(store_session, [event])
    store_session.commit()

    (row,) = pending_outbox(store_session)

    assert row["topic"] == MARKET_EVENT_TOPIC
    assert row["payload"] == event.to_dict()
    assert "stale" not in row["payload"]
    assert "age_seconds" not in row["payload"]


def test_a_delivered_row_leaves_the_pending_queue_once(store_session: Session) -> None:
    record_events(store_session, [_event()])
    store_session.commit()
    (row,) = pending_outbox(store_session)

    assert mark_published(store_session, [row["id"]]) == 1
    store_session.commit()
    assert pending_outbox(store_session) == ()
    # Marking twice is not a second delivery; the row is already published.
    assert mark_published(store_session, [row["id"]]) == 0


def test_the_outbox_can_be_skipped_when_only_storage_is_wanted(store_session: Session) -> None:
    assert record_events(store_session, [_event()], outbox=False) == 1
    store_session.commit()

    assert _rows(store_session, MarketEventRow) == 1
    assert _rows(store_session, MarketOutboxRow) == 0


def test_latest_returns_the_newest_observation_per_market(store_session: Session) -> None:
    stale = _event(source_seconds=0, price="1")
    fresh = _event(source_seconds=60, price="2")
    other_pair = _event(pair="Mint2", source_seconds=30, price="3")
    record_events(store_session, [stale, fresh, other_pair])
    store_session.commit()

    by_pair = {event.pair: event for event in latest_events(store_session)}

    assert by_pair["Mint1111111111111111111111111111111111111111"].price == Decimal("2")
    assert by_pair["Mint2"].price == Decimal("3")
    assert len(by_pair) == 2


def test_latest_reports_a_future_dated_observation_as_the_source_said(
    store_session: Session,
) -> None:
    """The store does not judge a clock; it returns the row and the reader ages it."""
    ahead = _event(source_seconds=600, ingest_seconds=601)
    record_events(store_session, [ahead])
    store_session.commit()

    (stored,) = latest_events(store_session)

    assert stored.source.observed_at == _at(600)
    assert stored.source.is_future(now=_at(0)) is True


def test_an_unwritable_store_reports_the_reason_instead_of_silently_dropping(
    store_session: Session,
) -> None:
    """A schema mismatch must surface, not be swallowed as "0 written".

    Dropping the table underneath the writer is the schema drift a deployment actually
    hits: a database that never ran the revision, or one behind head.
    """
    store_session.execute(sa.text("DROP TABLE market_events"))
    store_session.commit()

    with pytest.raises(MarketStoreError) as raised:
        record_events(store_session, [_event()])

    assert "could not persist market events" in str(raised.value)


def test_a_failed_transaction_commits_nothing(tmp_path: Path) -> None:
    """An unreachable database raises the driver's reason and writes nothing."""
    missing = tmp_path / "no-such-directory" / "tx.db"
    engine = sa.create_engine(f"sqlite+pysqlite:///{missing}")

    with pytest.raises(MarketStoreError) as raised:
        record_events_transactionally(engine, [_event()])

    assert "could not persist market events" in str(raised.value)
    assert not missing.exists()


def test_a_limit_must_be_positive(store_session: Session) -> None:
    with pytest.raises(ValueError, match="limit must be positive"):
        load_events(store_session, limit=0)


def test_a_stored_event_serves_the_existing_provenance_read_model_unchanged(
    store_session: Session,
) -> None:
    """The API's read model is reused, not reimplemented: a stored event ages the same way.

    ``market_reads.provenance`` already turns a ``SourceRef`` into the displayed age and the
    stale verdict. A row from this store carries a real ``SourceRef``, so the existing read
    model applies with no shape change and no second freshness rule.
    """
    from ezchain_api.services.market_reads import provenance

    event = _event(source_seconds=0, ingest_seconds=1)
    record_events(store_session, [event])
    store_session.commit()
    (stored,) = latest_events(store_session)

    fresh = provenance(stored.source, max_age_seconds=60, now=_at(30))
    stale = provenance(stored.source, max_age_seconds=60, now=_at(120))

    assert fresh.age_seconds == 30
    assert fresh.stale is False
    assert stale.age_seconds == 120
    assert stale.stale is True
    # The displayed age and the verdict come from one clock, exactly as the event's own
    # contract derives them.
    assert fresh.age_seconds == int(stored.age_seconds(_at(30)))


def test_a_row_with_no_source_clock_is_unknown_not_fresh(store_session: Session) -> None:
    """The read model's rule survives persistence: no source means no age, never zero."""
    from ezchain_api.services.market_reads import provenance, source_ref

    assert source_ref("", _at(0)) is None
    assert source_ref("unknown", _at(0)) is None
    assert provenance(None).age_seconds is None
    assert provenance(None).stale is None


# --------------------------------------------------------------------------------------
# Postgres: the deployment path. Skipped, never faked, when no test database is configured.
# --------------------------------------------------------------------------------------


def _postgres_url() -> str:
    url = os.environ.get("EZCHAIN_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("EZCHAIN_TEST_DATABASE_URL is not a Postgres test database")
    return url


@pytest.fixture
def postgres_engine() -> Iterator[Engine]:
    """A throwaway database on the test server, dropped afterwards."""
    base = _postgres_url()
    parsed = make_url(base)
    name = f"ezchain_market_store_{uuid4().hex[:8]}_test"
    admin = parsed.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{name}"')
    engine = sa.create_engine(parsed.set(database=name).render_as_string(hide_password=False))
    try:
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_postgres_round_trips_an_observation_exactly(postgres_engine: Engine) -> None:
    event = _event(confidence="0.125")
    assert record_events_transactionally(postgres_engine, [event]) == 1

    with postgres_engine.connect() as connection:
        (stored,) = load_events(connection)

    assert stored == event
    assert stored.to_dict() == event.to_dict()


def test_postgres_enforces_the_contract_identity_in_the_schema(
    postgres_engine: Engine,
) -> None:
    """A direct duplicate insert fails: the idempotency is the primary key, not a check."""
    event = _event()
    assert record_events_transactionally(postgres_engine, [event]) == 1

    with postgres_engine.begin() as connection:
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.insert(MarketEventRow).values(
                    source_name=event.source.name,
                    pair=event.pair,
                    source_observed_at=event.source.observed_at,
                    chain=event.chain,
                    venue=event.venue,
                    price=event.price,
                    size=event.size,
                    direction=event.direction,
                    units=event.units,
                    confidence=event.confidence,
                    source_url=event.source.url,
                    observed_at=event.observed_at,
                    schema_version=event.schema_version,
                )
            )


def test_postgres_records_a_batch_once_and_serves_latest(postgres_engine: Engine) -> None:
    events = [_event(source_seconds=index * 5, ingest_seconds=index * 5 + 1) for index in range(3)]
    assert record_events_transactionally(postgres_engine, events) == 3
    assert record_events_transactionally(postgres_engine, events) == 0

    with postgres_engine.connect() as connection:
        assert len(load_events(connection)) == 3
        (newest,) = latest_events(connection)
        assert newest.source.observed_at == _at(10)
        assert len(pending_outbox(connection)) == 3
