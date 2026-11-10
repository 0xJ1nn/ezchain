"""Create append-only protection events and add optimistic versioning.

Revision ID: 0012_protection_events
Revises: 0010_ledger_sequence
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_protection_events"
down_revision: str | None = "0011_idempotency_fingerprint"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Optimistic versioning on protection arms (additive).
    op.add_column(
        "protection_arms",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )

    op.create_table(
        "protection_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("arm_id", sa.String(length=36), nullable=False),
        # Monotonic within the arm: newest-first reads stay deterministic even
        # when two observations share observed_at.
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column(
            "rule_snapshot",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("mark", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("arm_id", "seq", name="uq_protection_event_seq"),
    )
    op.create_index(
        op.f("ix_protection_events_arm_id"), "protection_events", ["arm_id"], unique=False
    )
    op.create_index(op.f("ix_protection_events_seq"), "protection_events", ["seq"], unique=False)
    op.create_index(
        op.f("ix_protection_events_action"), "protection_events", ["action"], unique=False
    )
    op.create_index(
        op.f("ix_protection_events_observed_at"),
        "protection_events",
        ["observed_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_protection_events_order_id"), "protection_events", ["order_id"], unique=False
    )
    op.create_index(
        op.f("ix_protection_events_created_at"),
        "protection_events",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_protection_events_created_at"), table_name="protection_events")
    op.drop_index(op.f("ix_protection_events_order_id"), table_name="protection_events")
    op.drop_index(op.f("ix_protection_events_observed_at"), table_name="protection_events")
    op.drop_index(op.f("ix_protection_events_action"), table_name="protection_events")
    op.drop_index(op.f("ix_protection_events_seq"), table_name="protection_events")
    op.drop_index(op.f("ix_protection_events_arm_id"), table_name="protection_events")
    op.drop_table("protection_events")
    op.drop_column("protection_arms", "version")
