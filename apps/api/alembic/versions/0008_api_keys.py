"""Add scoped, hashed agent API keys.

Revision ID: 0008_api_keys
Revises: 0007_strategy_plugins
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_api_keys"
down_revision: str | None = "0007_strategy_plugins"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("subject", sa.String(length=120), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "scopes",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_api_keys_subject"), "api_keys", ["subject"], unique=False)
    op.create_index(op.f("ix_api_keys_token_hash"), "api_keys", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_api_keys_token_hash"), table_name="api_keys")
    op.drop_index(op.f("ix_api_keys_subject"), table_name="api_keys")
    op.drop_table("api_keys")
