"""Agent orders wait in an approval queue; the automation tier is per user.

Revision ID: 0021_approvals
Revises: 0020_market_events

Three tables, all additive. ``order_approvals`` holds an agent's order while a person
decides: the stored intent, the server's own risk evidence, the lifecycle status and the
person who decided. ``automation_settings`` is one row per user naming the tier
(``manual`` or ``full_auto``); a user with no row is ``manual``. ``automation_consents``
is append-only and records the risk disclosure a person accepted before ``full_auto`` was
enabled.

``paper_orders.status`` already carries a free-text status string, so ``pending_approval``
needs no column change. No existing row is rewritten or backfilled: an order written before
this revision has no approval, which is exactly what the read model reports for it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021_approvals"
down_revision: str | None = "0020_market_events"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

JSON_COLUMN = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _tables() -> set[str]:
    from sqlalchemy import inspect

    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "order_approvals" not in tables:
        op.create_table(
            "order_approvals",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("order_id", sa.String(36), sa.ForeignKey("paper_orders.id"), nullable=False),
            sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
            sa.Column("intent", JSON_COLUMN, nullable=True),
            sa.Column("risk_decision", JSON_COLUMN, nullable=True),
            sa.Column("execution_reason", sa.String(120), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("decided_by_kind", sa.String(16), nullable=True),
            sa.Column("decided_by_id", sa.String(120), nullable=True),
            sa.Column("decision_note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("order_id", name="uq_order_approval_order"),
        )
        op.create_index("ix_order_approvals_order_id", "order_approvals", ["order_id"])
        op.create_index("ix_order_approvals_status", "order_approvals", ["status"])
        op.create_index("ix_order_approvals_created_at", "order_approvals", ["created_at"])

    if "automation_settings" not in tables:
        op.create_table(
            "automation_settings",
            sa.Column("user_id", sa.String(120), primary_key=True),
            sa.Column("tier", sa.String(16), nullable=False, server_default="manual"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_by_kind", sa.String(16), nullable=True),
            sa.Column("updated_by_id", sa.String(120), nullable=True),
        )

    if "automation_consents" not in tables:
        op.create_table(
            "automation_consents",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(120), nullable=False),
            sa.Column("tier", sa.String(16), nullable=False),
            sa.Column("disclosure_version", sa.String(16), nullable=False),
            sa.Column("disclosure_text", sa.Text(), nullable=False),
            sa.Column("actor_kind", sa.String(16), nullable=True),
            sa.Column("actor_id", sa.String(120), nullable=True),
            sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_automation_consents_user_id", "automation_consents", ["user_id"])
        op.create_index(
            "ix_automation_consents_acknowledged_at", "automation_consents", ["acknowledged_at"]
        )


def downgrade() -> None:
    tables = _tables()
    if "automation_consents" in tables:
        op.drop_index("ix_automation_consents_acknowledged_at", table_name="automation_consents")
        op.drop_index("ix_automation_consents_user_id", table_name="automation_consents")
        op.drop_table("automation_consents")
    if "automation_settings" in tables:
        op.drop_table("automation_settings")
    if "order_approvals" in tables:
        op.drop_index("ix_order_approvals_created_at", table_name="order_approvals")
        op.drop_index("ix_order_approvals_status", table_name="order_approvals")
        op.drop_index("ix_order_approvals_order_id", table_name="order_approvals")
        op.drop_table("order_approvals")
