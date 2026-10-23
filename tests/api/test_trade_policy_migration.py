"""The trade-policy migration on the SQLite tail (CONTRACT I8)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from ezchain_api.db import alembic_config, database_revision, head_revision

SETTINGS_COLUMNS = {"key", "value", "updated_by", "updated_at"}
LEGACY_ORDER_ID = "legacy-order-0001"


def test_trade_policy_upgrades_a_sqlite_database(tmp_path: Path) -> None:
    database = tmp_path / "trade-policy.db"
    url = f"sqlite+pysqlite:///{database}"
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["database_url"] = url
    engine = create_engine(url)
    # The Postgres-only baseline cannot run on SQLite; seed the table this revision alters
    # and one pre-existing row, so the upgrade is proven to preserve legacy orders.
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE paper_orders ("
                "id VARCHAR(36) PRIMARY KEY, "
                "amount_usd TEXT NOT NULL"
                ")"
            )
        )
        connection.execute(
            text("INSERT INTO paper_orders (id, amount_usd) VALUES (:id, '42.00')"),
            {"id": LEGACY_ORDER_ID},
        )
    command.stamp(config, "0008_api_keys")
    command.upgrade(config, "head")
    try:
        inspector = inspect(engine)
        assert "app_settings" in inspector.get_table_names()
        settings_columns = {
            column["name"] for column in inspector.get_columns("app_settings")
        }
        assert settings_columns == SETTINGS_COLUMNS
        order_columns = {
            column["name"] for column in inspector.get_columns("paper_orders")
        }
        assert "risk_profile" in order_columns
        with Session(engine) as session:
            assert database_revision(session) == head_revision()
            assert session.execute(
                text("SELECT COUNT(*) FROM app_settings")
            ).scalar_one() == 0
            legacy = session.execute(
                text("SELECT amount_usd, risk_profile FROM paper_orders WHERE id = :id"),
                {"id": LEGACY_ORDER_ID},
            ).one()
            assert Decimal(legacy.amount_usd) == Decimal("42")
            assert legacy.risk_profile is None
    finally:
        engine.dispose()
