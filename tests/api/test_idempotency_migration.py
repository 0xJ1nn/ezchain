"""Migration 0011 adds the fingerprint without touching existing orders (CONTRACT I8)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from ezchain_api.db import alembic_config, database_revision, head_revision


def _config(url: str) -> Config:
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["database_url"] = url
    return config


def test_idempotency_fingerprint_upgrades_a_legacy_sqlite_database(tmp_path: Path) -> None:
    database = tmp_path / "idempotency.db"
    url = f"sqlite+pysqlite:///{database}"
    config = _config(url)
    engine = create_engine(url)
    # 0003 uses Postgres-only DDL; start at 0005 and run the tail, as the backtests
    # migration test does. Only 0011 touches paper_orders, so a minimal legacy table
    # is enough to prove the column is additive and existing rows keep a NULL hash.
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE strategies (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(
            text(
                "CREATE TABLE paper_orders ("
                "id VARCHAR(36) PRIMARY KEY, idempotency_key VARCHAR(120))"
            )
        )
        connection.execute(
            text("INSERT INTO paper_orders (id, idempotency_key) VALUES ('legacy', 'key-1')")
        )
    command.stamp(config, "0005_price_precision")
    command.upgrade(config, "head")
    try:
        columns = {column["name"]: column for column in inspect(engine).get_columns("paper_orders")}
        assert columns["request_fingerprint"]["nullable"] is True
        with engine.connect() as connection:
            legacy = connection.execute(
                text("SELECT request_fingerprint FROM paper_orders WHERE id = 'legacy'")
            ).one()
        assert legacy[0] is None
        with Session(engine) as session:
            assert database_revision(session) == head_revision() == "0011_idempotency_fingerprint"
    finally:
        engine.dispose()
