"""keep eighteen decimals on unit prices

Revision ID: 0005_price_precision
Revises: 0004_market_candles
Create Date: 2026-10-22

Unit prices of long-tail tokens reach 1e-10 USD. NUMERIC(38,8) rounded a $0.000000015
mark to $0.00000002 — a 33% error in every position value derived from it. Widening the
scale is lossless for existing rows. SQLite stores these columns as exact decimal text,
so there is nothing to alter there.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_price_precision"
down_revision: str | None = "0004_market_candles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# market_candles is listed too: databases that applied an early 0004 have scale 8 there;
# on a fresh database the alter is a no-op.
_COLUMNS = (
    ("paper_fills", "price_usd", False),
    ("market_snapshots", "price_usd", True),
    ("market_candles", "open", False),
    ("market_candles", "high", False),
    ("market_candles", "low", False),
    ("market_candles", "close", False),
)


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    for table, column, nullable in _COLUMNS:
        op.alter_column(
            table,
            column,
            type_=sa.Numeric(precision=38, scale=18),
            existing_type=sa.Numeric(precision=38, scale=8),
            existing_nullable=nullable,
        )


def downgrade() -> None:
    raise NotImplementedError("forward-only migration (CONTRACT: migrations are never destructive)")
