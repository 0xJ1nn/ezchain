"""Create position protection arms and profiles.

Revision ID: 0009_protection
Revises: 0008_api_keys
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_protection"
down_revision: str | None = "0008_api_keys"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "protection_arms",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("chain", sa.String(length=40), nullable=False),
        sa.Column("token_address", sa.String(length=160), nullable=False),
        sa.Column("token_symbol", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "params",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("peak_usd", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column(
            "triggered_levels",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("order_id", sa.String(length=36), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("profile_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_protection_arms_chain"), "protection_arms", ["chain"], unique=False)
    op.create_index(
        op.f("ix_protection_arms_token_address"), "protection_arms", ["token_address"], unique=False
    )
    op.create_index(op.f("ix_protection_arms_status"), "protection_arms", ["status"], unique=False)
    op.create_index(
        op.f("ix_protection_arms_order_id"), "protection_arms", ["order_id"], unique=False
    )
    op.create_index(
        op.f("ix_protection_arms_profile_id"), "protection_arms", ["profile_id"], unique=False
    )
    op.create_table(
        "protection_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("chain", sa.String(length=40), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column(
            "params",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "rules",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_protection_profiles_chain"), "protection_profiles", ["chain"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_protection_profiles_chain"), table_name="protection_profiles")
    op.drop_table("protection_profiles")
    op.drop_index(op.f("ix_protection_arms_profile_id"), table_name="protection_arms")
    op.drop_index(op.f("ix_protection_arms_order_id"), table_name="protection_arms")
    op.drop_index(op.f("ix_protection_arms_status"), table_name="protection_arms")
    op.drop_index(op.f("ix_protection_arms_token_address"), table_name="protection_arms")
    op.drop_index(op.f("ix_protection_arms_chain"), table_name="protection_arms")
    op.drop_table("protection_arms")
