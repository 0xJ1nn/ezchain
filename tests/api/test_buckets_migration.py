"""Migration 0023 adds the bucket cap and the ledger attribution additively (CONTRACT I8).

The revision is exercised in isolation: each test stamps at ``0022_strategy_creator`` with
the pre-0023 shape of the tables it touches, then upgrades exactly one revision.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ezchain_api.db import alembic_config, database_revision, head_revision
from ezchain_api.models import AgentBucketRow
from ezchain_api.services import paper

BUCKET_COLUMNS = {
    "agent_id",
    "owner",
    "cap_usd",
    "updated_at",
    "updated_by_kind",
    "updated_by_id",
}

LEDGER_BEFORE = (
    "CREATE TABLE ledger_entries ("
    "id VARCHAR(36) PRIMARY KEY, seq BIGINT, group_id VARCHAR(36), "
    "account VARCHAR(160), asset VARCHAR(160), amount TEXT, usd_value TEXT, "
    "kind VARCHAR(32), wallet_id VARCHAR(80), actor_kind VARCHAR(16), "
    "actor_id VARCHAR(120), created_at DATETIME)"
)


def _config(url: str) -> Config:
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["database_url"] = url
    return config


def _at_0022(tmp_path: Path, name: str) -> tuple[Engine, Config]:
    url = f"sqlite+pysqlite:///{tmp_path / name}"
    config = _config(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE strategies (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(
            text(
                "CREATE TABLE paper_orders (id VARCHAR(36) PRIMARY KEY, "
                "idempotency_key VARCHAR(120))"
            )
        )
        connection.execute(text(LEDGER_BEFORE))
    command.stamp(config, "0022_strategy_creator")
    return engine, config


def test_the_bucket_migration_adds_the_cap_and_the_ledger_attribution(tmp_path: Path) -> None:
    engine, config = _at_0022(tmp_path, "buckets.db")
    command.upgrade(config, "head")
    try:
        inspector = inspect(engine)
        assert "agent_buckets" in inspector.get_table_names()
        assert {column["name"] for column in inspector.get_columns("agent_buckets")} == (
            BUCKET_COLUMNS
        )
        assert "bucket_id" in {column["name"] for column in inspector.get_columns("ledger_entries")}
        indexes = {index["name"] for index in inspector.get_indexes("ledger_entries")}
        assert "ix_ledger_entries_bucket_id" in indexes
        with Session(engine) as session:
            assert database_revision(session) == head_revision() == "0024_agent_buckets"
    finally:
        engine.dispose()


def test_the_upgrade_keeps_existing_cash_unchanged(tmp_path: Path) -> None:
    """A leg written before the revision has no bucket, and its cash is untouched."""
    engine, config = _at_0022(tmp_path, "legacy_bucket.db")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO ledger_entries (id, seq, group_id, account, asset, amount, "
                "usd_value, kind, created_at) VALUES "
                "('legacy-cash', 1, 'genesis', 'cash:USD', 'USD', "
                "'10000', '10000', 'genesis', '2026-01-01 00:00:00')"
            )
        )
    command.upgrade(config, "head")
    try:
        with Session(engine) as session:
            rows = paper.ledger_rows(session)
            assert [row.bucket_id for row in rows] == [None]
            assert paper.project_ledger(rows).cash == Decimal("10000")
            assert paper.project_ledger(rows).bucket_cash == {}
    finally:
        engine.dispose()


def test_a_cap_row_round_trips_an_exact_decimal(tmp_path: Path) -> None:
    """SQLite stores money as TEXT, so the cap keeps every digit it was given."""
    engine, config = _at_0022(tmp_path, "cap_roundtrip.db")
    command.upgrade(config, "head")
    try:
        with Session(engine) as session:
            session.add(
                AgentBucketRow(agent_id="key-1", owner="alice", cap_usd=Decimal("1234.56789012"))
            )
            session.commit()
            stored = session.get(AgentBucketRow, "key-1")
            assert stored is not None
            assert Decimal(str(stored.cap_usd)) == Decimal("1234.56789012")
    finally:
        engine.dispose()


def test_the_downgrade_removes_only_what_it_added(tmp_path: Path) -> None:
    engine, config = _at_0022(tmp_path, "bucket_down.db")
    command.upgrade(config, "head")
    try:
        command.downgrade(config, "0022_strategy_creator")
        inspector = inspect(engine)
        assert "agent_buckets" not in inspector.get_table_names()
        columns = {column["name"] for column in inspector.get_columns("ledger_entries")}
        assert "bucket_id" not in columns
        assert "amount" in columns and "usd_value" in columns
    finally:
        engine.dispose()
