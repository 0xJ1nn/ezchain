"""Add the persisted trade-policy settings document.

Revision ID: 0009_trade_policy
Revises: 0008_api_keys

A forward-only, portable table: one JSON document per settings key, plus the audit
timestamp and principal. No existing table is altered, so an exported paper database
upgrades without a rewrite. The trade policy is not a ledger entry.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_trade_policy"
down_revision: str | None = "0008_api_keys"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column(
            "value",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.String(length=120), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    # Nullable: strategy-attributed orders and historical rows have no manual profile.
    op.add_column(
        "paper_orders",
        sa.Column("risk_profile", sa.String(length=8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("paper_orders", "risk_profile")
    op.drop_table("app_settings")
