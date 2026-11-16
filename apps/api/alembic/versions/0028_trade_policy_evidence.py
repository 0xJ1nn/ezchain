"""Persist the trade-policy sizing decision on orders and runs (GH-107).

Revision ID: 0028_trade_policy_evidence
Revises: 0027_no_risk_funding_audit

The order records the amount the server resolved, the profile that shaped it, the limits
snapshot it was measured against and whether the source was a person or a strategy. The
strategy run records the same profile and limits when a policy change produced it. Both
columns are nullable evidence; no existing row is rewritten.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028_trade_policy_evidence"
down_revision: str | None = "0027_no_risk_funding_audit"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

COLUMNS = {
    "paper_orders": "policy_json",
    "strategy_runs": "policy_json",
}


def _columns(table: str) -> set[str]:
    """A trimmed test database may not carry the table at all."""
    from sqlalchemy import inspect

    bind = op.get_bind()
    if table not in inspect(bind).get_table_names():
        return set()
    return {column["name"] for column in inspect(bind).get_columns(table)}


def upgrade() -> None:
    for table, column in COLUMNS.items():
        existing = _columns(table)
        if not existing or column in existing:
            continue
        op.add_column(
            table,
            sa.Column(
                column,
                sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
                nullable=True,
            ),
        )


def downgrade() -> None:
    for table, column in COLUMNS.items():
        if column in _columns(table):
            op.drop_column(table, column)
