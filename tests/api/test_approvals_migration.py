"""Migration 0021 adds the approval queue and the automation tier additively (CONTRACT I8)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from ezchain_api.db import alembic_config, database_revision, head_revision

TABLES = {
    "order_approvals": {
        "id",
        "order_id",
        "status",
        "intent",
        "risk_decision",
        "execution_reason",
        "decided_at",
        "decided_by_kind",
        "decided_by_id",
        "decision_note",
        "created_at",
        "expires_at",
        "executed_at",
    },
    "automation_settings": {
        "user_id",
        "tier",
        "updated_at",
        "updated_by_kind",
        "updated_by_id",
    },
    "automation_consents": {
        "id",
        "user_id",
        "tier",
        "disclosure_version",
        "disclosure_text",
        "actor_kind",
        "actor_id",
        "acknowledged_at",
    },
}


def _config(url: str) -> Config:
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["database_url"] = url
    return config


def test_the_approval_migration_creates_its_tables_additively(tmp_path: Path) -> None:
    database = tmp_path / "approvals.db"
    url = f"sqlite+pysqlite:///{database}"
    config = _config(url)
    engine = create_engine(url)
    # 0003 uses Postgres-only DDL, so stamp past it and run the tail, as the actor
    # migration test does.
    with engine.begin() as connection:
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
        tables = set(inspector.get_table_names())
        for table, expected in TABLES.items():
            assert table in tables, f"{table} was not created"
            columns = {column["name"] for column in inspector.get_columns(table)}
            assert columns == expected, f"{table} columns differ: {columns ^ expected}"
        unique = {
            (constraint["name"], tuple(constraint["column_names"]))
            for constraint in inspector.get_unique_constraints("order_approvals")
        }
        assert ("uq_order_approval_order", ("order_id",)) in unique
        # The legacy order is untouched: no approval is invented for it.
        with engine.connect() as connection:
            count = connection.execute(text("SELECT COUNT(*) FROM order_approvals")).scalar()
        assert count == 0
        with Session(engine) as session:
            assert database_revision(session) == head_revision()
    finally:
        engine.dispose()


def test_the_approval_migration_is_idempotent_on_a_second_upgrade(tmp_path: Path) -> None:
    database = tmp_path / "approvals_twice.db"
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
        before = {name for name in inspect(engine).get_table_names()}
        command.upgrade(config, "head")
        after = {name for name in inspect(engine).get_table_names()}
        assert before == after
        assert set(TABLES) <= after
    finally:
        engine.dispose()
