"""Migration 0026 indexes the stored market list's order (GH-104, CONTRACT I8).

The revision is exercised from the shape a database had at ``0025_strategy_run_window``:
both ordering indexes appear, the order they encode is the one the endpoint asks for, and a
database that already has them is left alone so a re-run is a no-op.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url

from ezchain_api.db import alembic_config, database_revision, head_revision

REVISION = "0027_no_risk_funding_audit"
TABLE = "market_snapshots"
GLOBAL_INDEX = "ix_market_snapshot_liquidity_order"
CHAIN_INDEX = "ix_market_snapshot_chain_liquidity_order"

BEFORE = (
    "CREATE TABLE market_snapshots ("
    "id VARCHAR(36) PRIMARY KEY, chain VARCHAR(40), token_address VARCHAR(160), "
    "liquidity_usd NUMERIC(38,8), observed_at DATETIME, raw JSON)"
)
SEED = (
    "INSERT INTO market_snapshots (id, chain, token_address, liquidity_usd, observed_at) "
    "VALUES ('r1', 'ethereum', '0xa', 10, '2026-11-14 00:00:00'), "
    "('r2', 'ethereum', '0xb', NULL, '2026-11-14 00:00:01'), "
    "('r3', 'solana', '0xc', 20, '2026-11-14 00:00:02')"
)


def _config(url: str) -> Config:
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["database_url"] = url
    return config


def _at_0025(tmp_path: Path, name: str) -> tuple[Engine, Config]:
    url = f"sqlite+pysqlite:///{tmp_path / name}"
    config = _config(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text(BEFORE))
        connection.execute(text(SEED))
    command.stamp(config, "0025_strategy_run_window")
    return engine, config


def _index_sql(engine: Engine) -> dict[str, str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = :table"),
            {"table": TABLE},
        ).all()
    return {row[0]: row[1] or "" for row in rows}


def test_the_upgrade_adds_both_ordering_indexes(tmp_path: Path) -> None:
    engine, config = _at_0025(tmp_path, "stored_order.db")
    command.upgrade(config, "head")
    try:
        indexes = _index_sql(engine)
        assert GLOBAL_INDEX in indexes
        assert CHAIN_INDEX in indexes
        # The order is the endpoint's, including the expression that puts unknown last.
        assert "liquidity_usd IS NULL" in indexes[GLOBAL_INDEX]
        assert "liquidity_usd DESC" in indexes[GLOBAL_INDEX]
        assert "observed_at DESC" in indexes[GLOBAL_INDEX]
        assert "id DESC" in indexes[GLOBAL_INDEX]
        assert indexes[CHAIN_INDEX].startswith(
            f"CREATE INDEX {CHAIN_INDEX} ON {TABLE} (chain, ((liquidity_usd IS NULL))"
        )
        with engine.connect() as connection:
            assert connection.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar() == 3
        from sqlalchemy.orm import Session

        with Session(engine) as session:
            assert database_revision(session) == head_revision() == REVISION
    finally:
        engine.dispose()


def test_the_downgrade_drops_them_and_keeps_the_rows(tmp_path: Path) -> None:
    engine, config = _at_0025(tmp_path, "stored_order_down.db")
    command.upgrade(config, "head")
    try:
        command.downgrade(config, "0025_strategy_run_window")
        indexes = _index_sql(engine)
        assert GLOBAL_INDEX not in indexes
        assert CHAIN_INDEX not in indexes
        with engine.connect() as connection:
            assert connection.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar() == 3
    finally:
        engine.dispose()


def test_a_table_without_the_ordered_columns_is_left_alone(tmp_path: Path) -> None:
    """The revision is a no-op on a trimmed table, which is how other migration tests seed."""
    url = f"sqlite+pysqlite:///{tmp_path / 'trimmed.db'}"
    config = _config(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE market_snapshots (id VARCHAR(36) PRIMARY KEY, "
                "chain VARCHAR(40), token_address VARCHAR(160), observed_at DATETIME)"
            )
        )
    command.stamp(config, "0025_strategy_run_window")
    command.upgrade(config, "head")
    try:
        names = {str(index["name"]) for index in inspect(engine).get_indexes(TABLE)}
        assert "ix_market_snapshot_liquidity_order" not in names
    finally:
        engine.dispose()


def _postgres_url() -> str:
    url = os.environ.get("EZCHAIN_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("EZCHAIN_TEST_DATABASE_URL is not a Postgres test database")
    return url


@pytest.fixture
def scratch_postgres() -> Iterator[str]:
    base = _postgres_url()
    parsed = make_url(base)
    name = f"ezchain_stored_order_{uuid4().hex[:8]}_test"
    admin = parsed.set(drivername="postgresql", database="postgres")
    with psycopg.connect(admin.render_as_string(hide_password=False), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    created = parsed.set(database=name)
    try:
        yield created.render_as_string(hide_password=False)
    finally:
        with psycopg.connect(admin.render_as_string(hide_password=False), autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_a_fresh_postgres_database_indexes_the_order(scratch_postgres: str) -> None:
    """Postgres builds the same expression index, and the order actually runs on it."""
    config = _config(scratch_postgres)
    command.upgrade(config, "head")
    engine = create_engine(scratch_postgres)
    try:
        definitions = {
            row[0]: row[1]
            for row in engine.connect()
            .execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE tablename = 'market_snapshots'"
                )
            )
            .all()
        }
        assert GLOBAL_INDEX in definitions
        assert CHAIN_INDEX in definitions
        assert "liquidity_usd IS NULL" in definitions[GLOBAL_INDEX]
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO market_snapshots "
                    "(id, chain, token_address, symbol, name, source, liquidity_usd, "
                    " observed_at, raw) "
                    "SELECT 'p-'||i, CASE WHEN i % 2 = 0 THEN 'ethereum' ELSE 'solana' END, "
                    "'0x'||i, 'S', 'S', 'dexscreener', (i * 7919) % 100000, now(), '{}' "
                    "FROM generate_series(1, 5000) i"
                )
            )
            connection.execute(text("ANALYZE market_snapshots"))
            plan = (
                connection.execute(
                    text(
                        "EXPLAIN SELECT id FROM market_snapshots "
                        "WHERE chain = 'ethereum' "
                        "ORDER BY (liquidity_usd IS NULL), liquidity_usd DESC, "
                        "observed_at DESC, id DESC LIMIT 5"
                    )
                )
                .scalars()
                .all()
            )
        assert any(CHAIN_INDEX in line for line in plan), plan
        assert not any("Sort" in line for line in plan), plan
    finally:
        engine.dispose()
