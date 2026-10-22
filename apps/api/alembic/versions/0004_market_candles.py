"""market_candles: closed OHLCV history for indicator-capable strategies

Revision ID: 0004_market_candles
Revises: 0003_paper_fill_order
Create Date: 2026-10-22

Forward-only and additive: a new table, no backfill, no change to an existing one. One row
per (chain, token_address, timeframe, open_time); re-fetching a bar updates it, so a
venue's revision of a just-closed bar replaces the stored value instead of duplicating it.
The forming bar is never written by the API service.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_market_candles"
down_revision: str | None = "0003_paper_fill_order"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_candles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("chain", sa.String(length=40), nullable=False),
        sa.Column("token_address", sa.String(length=160), nullable=False),
        sa.Column("pool_address", sa.String(length=160), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("high", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("low", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("close", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("volume", sa.Numeric(precision=38, scale=8), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "chain",
            "token_address",
            "timeframe",
            "open_time",
            name="uq_market_candle_bar",
        ),
    )


def downgrade() -> None:
    op.drop_table("market_candles")
