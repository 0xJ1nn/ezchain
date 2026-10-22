"""backtests: immutable backtest artifacts

Revision ID: 0006_backtests
Revises: 0005_price_precision
Create Date: 2026-10-23

Forward-only and additive: one new table, no backfill and no change to an existing one.
``strategy_id`` is deliberately not a foreign key. A backtest is a measurement of a graph
at a point in time; deleting the strategy must not delete the evidence (CONTRACT I9).
Request and result are stored as JSON exactly as they were produced, so replay does not
depend on the current catalogue or validator.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_backtests"
down_revision: str | None = "0005_price_precision"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backtests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("strategy_id", sa.String(length=36), nullable=True),
        sa.Column(
            "request",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "result",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_backtests_strategy_id"), "backtests", ["strategy_id"], unique=False)
    op.create_index(op.f("ix_backtests_created_at"), "backtests", ["created_at"], unique=False)


def downgrade() -> None:
    raise NotImplementedError("forward-only migration (CONTRACT: migrations are never destructive)")
