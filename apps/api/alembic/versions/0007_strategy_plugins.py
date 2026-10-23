"""Add the pinned plugin identity to strategies.

Revision ID: 0007_strategy_plugins
Revises: 0006_backtests
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_strategy_plugins"
down_revision: str | None = "0006_backtests"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("strategies", sa.Column("plugin_id", sa.String(length=128), nullable=True))
    op.add_column("strategies", sa.Column("plugin_version", sa.String(length=32), nullable=True))
    op.create_index("ix_strategies_plugin_id", "strategies", ["plugin_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_strategies_plugin_id", table_name="strategies")
    op.drop_column("strategies", "plugin_version")
    op.drop_column("strategies", "plugin_id")
