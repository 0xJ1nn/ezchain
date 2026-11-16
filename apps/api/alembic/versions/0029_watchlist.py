"""Persist the watchlist as a server entity (GH-33).

Revision ID: 0029_watchlist
Revises: 0028_trade_policy_evidence

One row per (chain, base_token) replaces the browser's localStorage copy so a watchlist
survives a restart and is shared by every surface. The row is identity only: no price, no
market value and no snapshot are cached, so a watched token with no observation is still a
row and can be reported as unknown instead of vanishing from a filtered read.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_watchlist"
down_revision: str | None = "0028_trade_policy_evidence"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

TABLE = "watchlist"


def upgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    if TABLE in inspect(bind).get_table_names():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("chain", sa.String(40), nullable=False),
        sa.Column("base_token", sa.String(160), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=True),
        sa.Column("name", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("chain", "base_token", name="uq_watchlist_chain_token"),
    )
    op.create_index("ix_watchlist_chain", TABLE, ["chain"])
    op.create_index("ix_watchlist_base_token", TABLE, ["base_token"])
    op.create_index("ix_watchlist_created_at", TABLE, ["created_at"])


def downgrade() -> None:
    from sqlalchemy import inspect

    if TABLE not in inspect(op.get_bind()).get_table_names():
        return
    op.drop_table(TABLE)
