"""Migration 0019 records the actor additively and leaves legacy rows unknown (CONTRACT I8)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from ezchain_api.db import alembic_config, database_revision, head_revision

COLUMNS = {
    "paper_orders": ("actor_kind", "actor_id"),
    "ledger_entries": ("actor_kind", "actor_id"),
}


def _config(url: str) -> Config:
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["database_url"] = url
    return config


def test_the_order_actor_migration_is_additive_and_forward_only(tmp_path: Path) -> None:
    database = tmp_path / "order_actor.db"
    url = f"sqlite+pysqlite:///{database}"
    config = _config(url)
    engine = create_engine(url)
    # 0003 uses Postgres-only DDL, so start past it and run the tail, as the paper wallet
    # migration test does. Only 0019 adds the actor columns.
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE ledger_entries ("
                "id VARCHAR(36) PRIMARY KEY, group_id VARCHAR(36), "
                "account VARCHAR(160), asset VARCHAR(160), amount TEXT, usd_value TEXT, "
                "kind VARCHAR(32), created_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE paper_orders ("
                "id VARCHAR(36) PRIMARY KEY, idempotency_key VARCHAR(120))"
            )
        )
        connection.execute(
            text("INSERT INTO paper_orders (id, idempotency_key) VALUES ('legacy', 'key-1')")
        )
    command.stamp(config, "0014_pair_identity")
    command.upgrade(config, "head")
    try:
        inspector = inspect(engine)
        for table, expected in COLUMNS.items():
            columns = {column["name"]: column for column in inspector.get_columns(table)}
            for name in expected:
                assert name in columns, f"{table}.{name} was not added"
                assert columns[name]["nullable"] is True
            indexes = {index["name"] for index in inspector.get_indexes(table)}
            assert f"ix_{table}_actor_kind" in indexes
        with engine.connect() as connection:
            legacy = connection.execute(
                text("SELECT actor_kind, actor_id FROM paper_orders WHERE id = 'legacy'")
            ).one()
        # Existing rows are not rewritten: they read as unknown, never a guessed actor.
        assert legacy[0] is None
        assert legacy[1] is None
        with Session(engine) as session:
            assert database_revision(session) == head_revision()
    finally:
        engine.dispose()


def test_the_order_actor_migration_is_idempotent_on_a_second_upgrade(tmp_path: Path) -> None:
    database = tmp_path / "order_actor_twice.db"
    url = f"sqlite+pysqlite:///{database}"
    config = _config(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE strategies (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(
            text(
                "CREATE TABLE paper_orders ("
                "id VARCHAR(36) PRIMARY KEY, idempotency_key VARCHAR(120))"
            )
        )
    command.stamp(config, "0005_price_precision")
    command.upgrade(config, "head")
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("paper_orders")}
        assert "actor_kind" in columns and "actor_id" in columns
        # A second run of the same revision is a no-op, not a duplicate-column failure.
        command.upgrade(config, "head")
        columns_after = {column["name"] for column in inspect(engine).get_columns("paper_orders")}
        assert columns_after == columns
    finally:
        engine.dispose()
