"""Retention: one validated policy, a dry run that deletes nothing, and no lost message.

The windows are the issue's: raw observations 7–30 days, 1-minute candles a year. The
policy refuses a number outside that range instead of clamping it, because a silently
narrowed window would delete evidence the operator did not agree to lose.

Pruning has two rules worth a test each. It ages an event by the *source's* timestamp, so a
late report cannot keep an old observation alive — and a message that was never published is
never deleted, because dropping it would lose a committed observation's delivery.
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
from ezchain_api.models import MarketCandleRow, MarketEventRow, MarketOutboxRow
from ezchain_core import MarketEvent, SourceRef
from ezchain_ingest import RetentionPolicy, RetentionPolicyError, RetentionResult, prune
from ezchain_ingest.retention import (
    DEFAULT_CANDLE_1M_DAYS,
    DEFAULT_RAW_DAYS,
    ENV_CANDLE_1M_DAYS,
    ENV_RAW_DAYS,
    MAX_RAW_DAYS,
    MIN_RAW_DAYS,
)

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _event(seconds_ago: float, *, ingest_seconds_ago: float | None = None) -> MarketEvent:
    observed = NOW - timedelta(seconds=seconds_ago)
    ingest = NOW - timedelta(seconds=ingest_seconds_ago or seconds_ago)
    return MarketEvent(
        chain="solana",
        venue="pumpfun",
        pair="Mint1111111111111111111111111111111111111111",
        price=Decimal("2"),
        size=None,
        direction=None,
        units="SOL",
        source=SourceRef("pumpfun-firehose", None, observed),
        observed_at=ingest,
    )


def _add_event(session: Session, event: MarketEvent) -> None:
    session.add(
        MarketEventRow(
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


def _add_outbox(session: Session, created: datetime, published: datetime | None) -> None:
    session.add(
        MarketOutboxRow(
            id=str(uuid4()),
            topic="market.event",
            payload={},
            created_at=created,
            published_at=published,
            attempts=0,
        )
    )


def _add_candle(session: Session, open_time: datetime, timeframe: str = "1m") -> None:
    session.add(
        MarketCandleRow(
            id=str(uuid4()),
            chain="solana",
            token_address="Mint1111111111111111111111111111111111111111",
            pool_address="pool",
            timeframe=timeframe,
            open_time=open_time,
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            volume=None,
            source="geckoterminal",
            fetched_at=open_time,
        )
    )


def _count(session: Session, entity: type[object]) -> int:
    return int(session.execute(sa.select(sa.func.count()).select_from(entity)).scalar_one())


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'retention.db'}")
    Base.metadata.create_all(engine)
    try:
        with sessionmaker(bind=engine, expire_on_commit=False)() as db:
            yield db
    finally:
        engine.dispose()


def test_the_default_policy_is_inside_the_window_the_issue_fixes() -> None:
    policy = RetentionPolicy()

    assert MIN_RAW_DAYS <= policy.raw_days <= MAX_RAW_DAYS
    assert policy.candle_1m_days == 365
    assert policy.to_dict() == {
        "raw_days": DEFAULT_RAW_DAYS,
        "candle_1m_days": DEFAULT_CANDLE_1M_DAYS,
    }


@pytest.mark.parametrize("raw_days", [0, MIN_RAW_DAYS - 1, MAX_RAW_DAYS + 1, -1])
def test_a_raw_window_outside_the_range_is_refused(raw_days: int) -> None:
    with pytest.raises(RetentionPolicyError, match="raw retention must be between"):
        RetentionPolicy(raw_days=raw_days)


def test_a_candle_window_outside_the_range_is_refused() -> None:
    with pytest.raises(RetentionPolicyError, match="1m candle retention"):
        RetentionPolicy(candle_1m_days=0)


def test_a_boolean_is_not_a_number_of_days() -> None:
    with pytest.raises(RetentionPolicyError):
        RetentionPolicy(raw_days=True)  # type: ignore[arg-type]


def test_the_policy_reads_one_home_and_keeps_the_default_when_unset() -> None:
    assert RetentionPolicy.from_env({}).raw_days == DEFAULT_RAW_DAYS
    assert RetentionPolicy.from_env({ENV_RAW_DAYS: ""}).raw_days == DEFAULT_RAW_DAYS
    assert RetentionPolicy.from_env({ENV_RAW_DAYS: "7"}).raw_days == 7
    assert RetentionPolicy.from_env({ENV_CANDLE_1M_DAYS: "30"}).candle_1m_days == 30


@pytest.mark.parametrize("value", ["not-a-number", "3.5"])
def test_a_malformed_window_is_refused_with_its_name(value: str) -> None:
    with pytest.raises(RetentionPolicyError, match=ENV_RAW_DAYS):
        RetentionPolicy.from_env({ENV_RAW_DAYS: value})


def test_a_dry_run_reports_what_it_would_delete_and_deletes_nothing(session: Session) -> None:
    _add_event(session, _event(seconds_ago=timedelta(days=40).total_seconds()))
    _add_event(session, _event(seconds_ago=60))
    session.commit()

    planned = prune(session, RetentionPolicy(raw_days=7), now=NOW)

    assert isinstance(planned, RetentionResult)
    assert planned.applied is False
    assert planned.raw_events == 1
    assert _count(session, MarketEventRow) == 2
    assert planned.raw_cutoff == NOW - timedelta(days=7)


def test_applying_deletes_only_the_expired_observation(session: Session) -> None:
    _add_event(session, _event(seconds_ago=timedelta(days=40).total_seconds()))
    _add_event(session, _event(seconds_ago=60))
    session.commit()

    result = prune(session, RetentionPolicy(raw_days=7), now=NOW, apply=True)
    session.commit()

    assert result.applied is True
    assert result.raw_events == 1
    assert _count(session, MarketEventRow) == 1


def test_an_event_is_aged_by_the_source_clock_not_the_ingest_clock(session: Session) -> None:
    """A late report of an old observation is old; a second's ingest lag does not save it."""
    _add_event(
        session,
        _event(
            seconds_ago=timedelta(days=40).total_seconds(),
            ingest_seconds_ago=1,
        ),
    )
    session.commit()

    result = prune(session, RetentionPolicy(raw_days=7), now=NOW, apply=True)
    session.commit()

    assert result.raw_events == 1
    assert _count(session, MarketEventRow) == 0


def test_a_fresh_event_survives_even_with_a_stale_source(session: Session) -> None:
    _add_event(
        session,
        _event(
            seconds_ago=1,
            ingest_seconds_ago=timedelta(days=40).total_seconds(),
        ),
    )
    session.commit()

    result = prune(session, RetentionPolicy(raw_days=7), now=NOW, apply=True)
    session.commit()

    assert result.raw_events == 0
    assert _count(session, MarketEventRow) == 1


def test_an_unpublished_message_is_never_deleted(session: Session) -> None:
    old = NOW - timedelta(days=40)
    _add_outbox(session, old, published=None)
    _add_outbox(session, old, published=old + timedelta(minutes=1))
    _add_outbox(session, NOW - timedelta(minutes=1), published=None)
    session.commit()

    result = prune(session, RetentionPolicy(raw_days=7), now=NOW, apply=True)
    session.commit()

    assert result.published_outbox == 1
    remaining = session.execute(
        sa.select(MarketOutboxRow.published_at).order_by(MarketOutboxRow.created_at)
    ).all()
    # The expired delivered row is gone; both unpublished rows remain, delivered or not.
    assert len(remaining) == 2
    assert all(row.published_at is None or row.published_at >= old for row in remaining)


def test_only_one_minute_bars_age_out_and_coarser_bars_stay(session: Session) -> None:
    old = NOW - timedelta(days=400)
    _add_candle(session, old, "1m")
    _add_candle(session, old, "1h")
    _add_candle(session, NOW - timedelta(minutes=2), "1m")
    session.commit()

    result = prune(session, RetentionPolicy(candle_1m_days=365), now=NOW, apply=True)
    session.commit()

    assert result.candles_1m == 1
    assert result.candle_table_present is True
    kept = session.execute(sa.select(MarketCandleRow.timeframe).order_by(MarketCandleRow.timeframe))
    assert [row.timeframe for row in kept] == ["1h", "1m"]


def test_retention_reports_a_missing_candle_table_rather_than_failing(tmp_path: Path) -> None:
    """A database that never ran the candle revision has no bars to prune, not an error."""
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'bare.db'}")
    MarketEventRow.__table__.create(engine)
    MarketOutboxRow.__table__.create(engine)
    try:
        with engine.begin() as connection:
            result = prune(connection, RetentionPolicy(raw_days=7), now=NOW)
        assert result.candle_table_present is False
        assert result.candles_1m == 0
    finally:
        engine.dispose()


def test_the_environment_is_read_when_no_policy_is_passed(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_RAW_DAYS, "7")
    _add_event(session, _event(seconds_ago=timedelta(days=10).total_seconds()))
    _add_event(session, _event(seconds_ago=timedelta(days=6).total_seconds()))
    session.commit()

    result = prune(session, now=NOW, apply=True)

    assert result.raw_cutoff == NOW - timedelta(days=7)
    assert result.raw_events == 1


def test_retention_is_safe_to_run_twice(session: Session) -> None:
    _add_event(session, _event(seconds_ago=timedelta(days=40).total_seconds()))
    session.commit()

    first = prune(session, RetentionPolicy(raw_days=7), now=NOW, apply=True)
    second = prune(session, RetentionPolicy(raw_days=7), now=NOW, apply=True)
    session.commit()

    assert first.raw_events == 1
    assert second.raw_events == 0


def test_the_operator_command_plans_by_default_and_refuses_a_bad_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default is a dry run: an operator reads the count before anything is deleted."""
    from ezchain_api.config import get_settings
    from ezchain_ingest.__main__ import main

    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'cli.db'}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        _add_event(db, _event(seconds_ago=timedelta(days=40).total_seconds()))
        db.commit()
    engine.dispose()

    monkeypatch.setenv("EZCHAIN_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'cli.db'}")
    get_settings.cache_clear()
    try:
        assert main([]) == 0

        engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'cli.db'}")
        try:
            with sessionmaker(bind=engine)() as db:
                # The dry run deleted nothing.
                assert _count(db, MarketEventRow) == 1
        finally:
            engine.dispose()

        assert main(["--raw-days", "3"]) == 2
    finally:
        get_settings.cache_clear()


# --------------------------------------------------------------------------------------
# Postgres: the deployment path. Skipped, never faked, when no test database is configured.
# --------------------------------------------------------------------------------------


@pytest.fixture
def postgres_session() -> Iterator[Session]:
    base = os.environ.get("EZCHAIN_TEST_DATABASE_URL", "")
    if not base.startswith("postgresql"):
        pytest.skip("EZCHAIN_TEST_DATABASE_URL is not a Postgres test database")
    parsed = make_url(base)
    name = f"ezchain_retention_{uuid4().hex[:8]}_test"
    admin = parsed.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{name}"')
    engine: Engine = sa.create_engine(
        parsed.set(database=name).render_as_string(hide_password=False)
    )
    Base.metadata.create_all(engine)
    try:
        with sessionmaker(bind=engine, expire_on_commit=False)() as db:
            yield db
    finally:
        engine.dispose()
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_postgres_prune_deletes_the_expired_rows_it_reported(postgres_session: Session) -> None:
    _add_event(postgres_session, _event(seconds_ago=timedelta(days=40).total_seconds()))
    _add_event(postgres_session, _event(seconds_ago=60))
    _add_candle(postgres_session, NOW - timedelta(days=400), "1m")
    _add_candle(postgres_session, NOW - timedelta(days=400), "1h")
    postgres_session.commit()

    result = prune(
        postgres_session, RetentionPolicy(raw_days=7, candle_1m_days=365), now=NOW, apply=True
    )
    postgres_session.commit()

    assert (result.raw_events, result.candles_1m) == (1, 1)
    assert _count(postgres_session, MarketEventRow) == 1
    assert _count(postgres_session, MarketCandleRow) == 1
