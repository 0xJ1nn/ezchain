"""Add position protection rules, profiles and audit events.

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
        "protection_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("profile_type", sa.String(length=24), nullable=False),
        sa.Column("chain", sa.String(length=40), nullable=True),
        sa.Column(
            "defaults",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_protection_profiles_chain", "protection_profiles", ["chain"], unique=False)

    op.create_table(
        "protections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("chain", sa.String(length=40), nullable=False),
        sa.Column("token_address", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column(
            "params",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("profile_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("trailing_activated", sa.Boolean(), nullable=False),
        sa.Column("trailing_peak", sa.Numeric(38, 18), nullable=True),
        sa.Column("trailing_rung_index", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_protection_idempotency"),
    )
    op.create_index("ix_protections_chain", "protections", ["chain"], unique=False)
    op.create_index("ix_protections_token_address", "protections", ["token_address"], unique=False)
    op.create_index(
        "ix_protection_chain_token", "protections", ["chain", "token_address"], unique=False
    )

    op.create_table(
        "protection_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("protection_id", sa.String(length=36), nullable=True),
        sa.Column("chain", sa.String(length=40), nullable=False),
        sa.Column("token_address", sa.String(length=160), nullable=False),
        sa.Column(
            "rule_snapshot",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("mark", sa.Numeric(38, 18), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_protection_events_protection_id", "protection_events", ["protection_id"], unique=False
    )
    op.create_index("ix_protection_events_chain", "protection_events", ["chain"], unique=False)
    op.create_index(
        "ix_protection_events_token_address", "protection_events", ["token_address"], unique=False
    )
    op.create_index(
        "ix_protection_events_order_id", "protection_events", ["order_id"], unique=False
    )


def downgrade() -> None:
    op.drop_table("protection_events")
    op.drop_table("protections")
    op.drop_table("protection_profiles")
