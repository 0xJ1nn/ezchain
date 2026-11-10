"""Add structured risk rejection reasons to paper orders.

Revision ID: 0013_risk_reasons
Revises: 0012_protection_events
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_risk_reasons"
down_revision: str | None = "0012_protection_events"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "paper_orders",
        sa.Column(
            "reasons",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("paper_orders", "reasons")
