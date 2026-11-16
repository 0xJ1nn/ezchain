"""Audit a paper no-risk funding decision on the order (GH-106).

Revision ID: 0027_no_risk_funding_audit
Revises: 0026_market_stored_order

The funding journal group already records the exact shortfall as money. This nullable
column records the rest of the decision — the trigger, the cash before and after the
top-up and the cap it was measured against — on the order that caused it. It is evidence,
not an account: no balance is stored and no existing row is rewritten.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027_no_risk_funding_audit"
down_revision: str | None = "0026_market_stored_order"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    """A trimmed test database may not carry the table at all."""
    from sqlalchemy import inspect

    bind = op.get_bind()
    if table not in inspect(bind).get_table_names():
        return set()
    return {column["name"] for column in inspect(bind).get_columns(table)}


def upgrade() -> None:
    columns = _columns("paper_orders")
    if not columns or "funding_json" in columns:
        return
    op.add_column(
        "paper_orders",
        sa.Column(
            "funding_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    if "funding_json" in _columns("paper_orders"):
        op.drop_column("paper_orders", "funding_json")
