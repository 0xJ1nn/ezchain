"""Unit prices keep eighteen decimals (migration 0005).

Long-tail tokens trade near 1e-10 USD. With eight decimals a stored mark of
0.000000015 became 0.00000002 and every position value derived from it moved by a
third. This guards the column types in the model and on a migrated Postgres database.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from ezchain_api.db import alembic_config
from ezchain_api.models import MarketCandleRow, MarketSnapshotRow, PaperFillRow

TINY = Decimal("0.000000000015000001")


@pytest.mark.parametrize(
    "column",
    [
        PaperFillRow.__table__.c.price_usd,
        MarketSnapshotRow.__table__.c.price_usd,
        MarketCandleRow.__table__.c.open,
        MarketCandleRow.__table__.c.close,
    ],
)
def test_price_columns_keep_eighteen_decimals(column: object) -> None:
    assert column.type.scale == 18  # type: ignore[attr-defined]


@pytest.fixture
def migrated_postgres() -> Iterator[str]:
    import os

    base = os.environ.get("EZCHAIN_TEST_DATABASE_URL", "")
    if not base.startswith("postgresql"):
        pytest.skip("EZCHAIN_TEST_DATABASE_URL is not a Postgres test database")
    parsed = make_url(base)
    name = f"ezchain_price_{uuid4().hex[:8]}_test"
    admin = parsed.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = parsed.set(database=name).render_as_string(hide_password=False)
    try:
        config = alembic_config()
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "head")
        yield url
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_postgres_round_trips_a_sub_cent_price(migrated_postgres: str) -> None:
    engine = create_engine(migrated_postgres)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO market_snapshots (id, chain, token_address, symbol, name,"
                    " price_usd, source, observed_at, raw)"
                    " VALUES ('s1', 'solana', 'mint', 'TINY', 'Tiny', :p, 'test', now(), '{}')"
                ),
                {"p": TINY},
            )
            stored = conn.execute(
                text("SELECT price_usd FROM market_snapshots WHERE id = 's1'")
            ).scalar_one()
        assert Decimal(stored) == TINY
    finally:
        engine.dispose()
