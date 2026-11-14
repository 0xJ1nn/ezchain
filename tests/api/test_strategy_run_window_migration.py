"""Migration 0025 makes the run log the decision claim for one window (CONTRACT I8, I9).

The revision is exercised from the shape a database had at ``0024_agent_buckets``: the
column is added, the unique pair is enforced, and rows that pre-date the claim keep their
NULL window untouched.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ezchain_api.db import alembic_config, database_revision, head_revision

TABLE = "strategy_runs"
COLUMN = "decision_window"
CONSTRAINT = "uq_strategy_run_window"

BEFORE = (
    "CREATE TABLE strategy_runs ("
    "id VARCHAR(36) PRIMARY KEY, strategy_id VARCHAR(36) NOT NULL, "
    "action VARCHAR(16) NOT NULL, reason VARCHAR(64) NOT NULL, inputs JSON NOT NULL, "
    "order_id VARCHAR(36), created_at DATETIME NOT NULL)"
)
# The table's own indexes, so the SQLite batch rebuild can be shown to keep them.
INDEXES = ("ix_strategy_runs_strategy_id", "ix_strategy_runs_created_at")


def _config(url: str) -> Config:
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["database_url"] = url
    return config


def _at_0024(tmp_path: Path, name: str) -> tuple[Engine, Config]:
    url = f"sqlite+pysqlite:///{tmp_path / name}"
    config = _config(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE strategies (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE paper_orders (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(text(BEFORE))
        for index in INDEXES:
            column = index.removeprefix("ix_strategy_runs_")
            connection.execute(text(f"CREATE INDEX {index} ON {TABLE} ({column})"))
    command.stamp(config, "0024_agent_buckets")
    return engine, config


def _insert_run(engine: Engine, run_id: str, window: str | None) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO strategy_runs "
                "(id, strategy_id, action, reason, inputs, decision_window, created_at) "
                "VALUES (:id, 'strategy-1', 'buy', 'MOMENTUM_ENTRY', '{}', :window, "
                "'2026-11-14 00:00:00')"
            ),
            {"id": run_id, "window": window},
        )


def _insert_legacy_run(engine: Engine, run_id: str) -> None:
    """A row written before the column existed: no decision window at all."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO strategy_runs "
                "(id, strategy_id, action, reason, inputs, created_at) "
                "VALUES (:id, 'strategy-1', 'buy', 'MOMENTUM_ENTRY', '{}', "
                "'2026-11-14 00:00:00')"
            ),
            {"id": run_id},
        )


def test_the_window_migration_adds_the_claim_to_a_preserved_table(tmp_path: Path) -> None:
    engine, config = _at_0024(tmp_path, "run_window.db")
    _insert_legacy_run(engine, "run-legacy")
    command.upgrade(config, "head")
    try:
        inspector = inspect(engine)
        assert COLUMN in {column["name"] for column in inspector.get_columns(TABLE)}
        assert CONSTRAINT in {item["name"] for item in inspector.get_unique_constraints(TABLE)}
        indexes = {index["name"] for index in inspector.get_indexes(TABLE)}
        assert set(INDEXES) <= indexes
        with engine.connect() as connection:
            windows = connection.execute(text(f"SELECT {COLUMN} FROM {TABLE}")).scalars().all()
        # A row that pre-dates the claim is never rewritten.
        assert windows == [None]
    finally:
        engine.dispose()


def test_one_window_accepts_one_row_and_any_number_of_null_windows(tmp_path: Path) -> None:
    engine, config = _at_0024(tmp_path, "run_window_unique.db")
    command.upgrade(config, "head")
    try:
        _insert_run(engine, "run-window", "strategy:strategy-1:42")
        # A row that consumed nothing stays retryable: NULL never collides.
        _insert_run(engine, "run-hold-a", None)
        _insert_run(engine, "run-hold-b", None)
        with pytest.raises(IntegrityError):
            _insert_run(engine, "run-duplicate", "strategy:strategy-1:42")
    finally:
        engine.dispose()


def test_the_downgrade_removes_only_the_claim(tmp_path: Path) -> None:
    engine, config = _at_0024(tmp_path, "run_window_down.db")
    _insert_legacy_run(engine, "run-legacy")
    command.upgrade(config, "head")
    try:
        command.downgrade(config, "0024_agent_buckets")
        inspector = inspect(engine)
        assert COLUMN not in {column["name"] for column in inspector.get_columns(TABLE)}
        assert CONSTRAINT not in {item["name"] for item in inspector.get_unique_constraints(TABLE)}
        with engine.connect() as connection:
            assert connection.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar() == 1
        with Session(engine) as session:
            assert database_revision(session) == "0024_agent_buckets"
            assert head_revision() == "0025_strategy_run_window"
    finally:
        engine.dispose()
