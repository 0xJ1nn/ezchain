"""Agent permission modes and their decision log.

Revision ID: 0032_agent_permission_modes
Revises: 0031_pair_address_width

Two additive tables: one row per owner's current mode, and an append-only log of the
permission decisions that gated agent calls. No existing row is changed or removed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0032_agent_permission_modes"
down_revision: str | None = "0031_pair_address_width"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(38, 8).with_variant(sa.Text(), "sqlite")


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "agent_permission_modes" not in tables:
        op.create_table(
            "agent_permission_modes",
            sa.Column("user_id", sa.String(120), primary_key=True),
            sa.Column("mode", sa.String(16), nullable=False, server_default="ask"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_by_kind", sa.String(16), nullable=True),
            sa.Column("updated_by_id", sa.String(120), nullable=True),
        )
    if "agent_permission_decisions" not in tables:
        op.create_table(
            "agent_permission_decisions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(120), nullable=False),
            sa.Column("effect", sa.String(16), nullable=False),
            sa.Column("mode", sa.String(16), nullable=False),
            sa.Column("outcome", sa.String(16), nullable=False),
            sa.Column("code", sa.String(64), nullable=False),
            sa.Column("amount_usd", MONEY, nullable=True),
            sa.Column("order_id", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_agent_permission_decisions_user_id",
            "agent_permission_decisions",
            ["user_id"],
        )
        op.create_index(
            "ix_agent_permission_decisions_created_at",
            "agent_permission_decisions",
            ["created_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "agent_permission_decisions" in tables:
        op.drop_table("agent_permission_decisions")
    if "agent_permission_modes" in tables:
        op.drop_table("agent_permission_modes")
