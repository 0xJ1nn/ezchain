"""Persist pair identity, DEX and pair age with market snapshots.

Revision ID: 0014_pair_identity
Revises: 0013_risk_reasons
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_pair_identity"
down_revision: str | None = "0013_risk_reasons"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    if "market_snapshots" not in inspect(bind).get_table_names():
        return
    op.add_column("market_snapshots", sa.Column("pair_address", sa.String(160), nullable=True))
    op.add_column("market_snapshots", sa.Column("dex", sa.String(40), nullable=True))
    op.add_column("market_snapshots", sa.Column("base_token_address", sa.String(160), nullable=True))
    op.add_column("market_snapshots", sa.Column("quote_token_address", sa.String(160), nullable=True))
    op.add_column("market_snapshots", sa.Column("pair_created_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("market_snapshots", sa.Column("url", sa.Text(), nullable=True))


def downgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    if "market_snapshots" not in inspect(bind).get_table_names():
        return
    op.drop_column("market_snapshots", "url")
    op.drop_column("market_snapshots", "pair_created_at")
    op.drop_column("market_snapshots", "quote_token_address")
    op.drop_column("market_snapshots", "base_token_address")
    op.drop_column("market_snapshots", "dex")
    op.drop_column("market_snapshots", "pair_address")
