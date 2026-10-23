"""Persist the trade-size resolution on orders and strategy runs.

Revision ID: 0010_size_resolution
Revises: 0009_trade_policy

Forward-only and additive: two nullable JSON columns. A legacy order or a strategy run
that never reached sizing keeps NULL; no existing row is rewritten. The document holds
decimal strings, the effective limits intersection, the policy source, server equity,
the manual/strategy source and the quantization flag, so a size can be replayed without
reading the current policy.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_size_resolution"
down_revision: str | None = "0009_trade_policy"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("paper_orders", sa.Column("size_resolution", JSON_TYPE, nullable=True))
    op.add_column("strategy_runs", sa.Column("size_resolution", JSON_TYPE, nullable=True))


def downgrade() -> None:
    op.drop_column("strategy_runs", "size_resolution")
    op.drop_column("paper_orders", "size_resolution")
