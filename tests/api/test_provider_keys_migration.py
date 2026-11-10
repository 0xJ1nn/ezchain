"""The provider-key migration on the SQLite tail."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from ezchain_api.db import alembic_config, database_revision, head_revision

COLUMNS = {
    "id",
    "workspace_id",
    "provider",
    "label",
    "fingerprint",
    "secret_ciphertext",
    "created_at",
    "last_used_at",
    "revoked_at",
}


def test_provider_keys_upgrades_a_sqlite_database(tmp_path: Path) -> None:
    database = tmp_path / "provider_keys.db"
    url = f"sqlite+pysqlite:///{database}"
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["database_url"] = url
    engine = create_engine(url)
    # The Postgres-only baseline cannot run on SQLite; seed the tables 0007 alters.
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE strategies (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(
            text(
                "CREATE TABLE paper_orders "
                "(id VARCHAR(36) PRIMARY KEY, idempotency_key VARCHAR(120))"
            )
        )
    command.stamp(config, "0007_strategy_plugins")
    command.upgrade(config, "head")
    try:
        inspector = inspect(engine)
        assert "provider_keys" in inspector.get_table_names()
        columns = {column["name"]: column for column in inspector.get_columns("provider_keys")}
        assert set(columns) == COLUMNS
        assert columns["secret_ciphertext"]["nullable"] is False
        with Session(engine) as session:
            assert database_revision(session) == head_revision()
    finally:
        engine.dispose()
