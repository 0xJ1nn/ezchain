"""The scoped-key migration on the SQLite tail (CONTRACT I8)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from ezchain_api.db import alembic_config, database_revision, head_revision

COLUMNS = {
    "id",
    "subject",
    "label",
    "token_hash",
    "scopes",
    "created_at",
    "last_used_at",
    "expires_at",
    "revoked_at",
}


def test_api_keys_upgrades_a_sqlite_database(tmp_path: Path) -> None:
    database = tmp_path / "keys.db"
    url = f"sqlite+pysqlite:///{database}"
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["database_url"] = url
    engine = create_engine(url)
    # The Postgres-only baseline cannot run on SQLite; seed the table 0007 alters, plus
    # ``paper_orders`` because a later trade-policy revision adds a column to it.
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE strategies (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(
            text("CREATE TABLE paper_orders (id VARCHAR(36) PRIMARY KEY, amount_usd TEXT)")
        )
        connection.execute(text("CREATE TABLE strategy_runs (id VARCHAR(36) PRIMARY KEY)"))
    command.stamp(config, "0007_strategy_plugins")
    command.upgrade(config, "head")
    try:
        inspector = inspect(engine)
        assert "api_keys" in inspector.get_table_names()
        columns = {column["name"]: column for column in inspector.get_columns("api_keys")}
        assert set(columns) == COLUMNS
        assert columns["token_hash"]["nullable"] is False
        unique = [
            index["column_names"]
            for index in inspector.get_indexes("api_keys")
            if index.get("unique")
        ]
        assert ["token_hash"] in unique
        with Session(engine) as session:
            assert database_revision(session) == head_revision()
    finally:
        engine.dispose()
