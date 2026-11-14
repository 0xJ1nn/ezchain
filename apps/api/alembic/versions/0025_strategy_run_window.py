"""Make the strategy run log the decision claim for one window (GH-129).

Revision ID: 0025_strategy_run_window
Revises: 0024_agent_buckets

Forward-only and additive: one nullable column and one unique constraint, no backfill.

The defect this closes: two evaluators of the same strategy could both pass the interval
gate, both place an order (the order idempotency key collapsed that) and both append a
``strategy_runs`` row, so the decision log overstated activity for one order and a restart
inside the same window could append again (CONTRACT I9). ``decision_window`` names the
interval bucket a decision consumes; the unique ``(strategy_id, decision_window)`` pair
makes the run insert itself the claim, so the evaluator that loses the race reports the
winner's record instead of inventing a second one.

Existing rows keep a NULL ``decision_window``: they pre-date the claim and are never
rewritten. ``NULL`` never collides in a unique constraint on either dialect, so the rule
applies to new rows only, and a row that does not consume the window (interval not
elapsed, a fail-closed hold) also stores NULL and stays retryable.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_strategy_run_window"
down_revision: str | None = "0024_agent_buckets"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

TABLE = "strategy_runs"
COLUMN = "decision_window"
CONSTRAINT = "uq_strategy_run_window"


def upgrade() -> None:
    from sqlalchemy import inspect

    inspector = inspect(op.get_bind())
    if TABLE not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns(TABLE)}
    if COLUMN not in columns:
        op.add_column(TABLE, sa.Column(COLUMN, sa.String(length=120), nullable=True))
    names = {item["name"] for item in inspector.get_unique_constraints(TABLE)}
    if CONSTRAINT not in names:
        # SQLite cannot add a constraint in place; batch mode rebuilds the table, which is
        # what a desktop database needs. Postgres adds the constraint on the table.
        with op.batch_alter_table(TABLE) as batch:
            batch.create_unique_constraint(CONSTRAINT, ["strategy_id", COLUMN])


def downgrade() -> None:
    from sqlalchemy import inspect

    inspector = inspect(op.get_bind())
    if TABLE not in set(inspector.get_table_names()):
        return
    names = {item["name"] for item in inspector.get_unique_constraints(TABLE)}
    if CONSTRAINT in names:
        with op.batch_alter_table(TABLE) as batch:
            batch.drop_constraint(CONSTRAINT, type_="unique")
    columns = {column["name"] for column in inspector.get_columns(TABLE)}
    if COLUMN in columns:
        with op.batch_alter_table(TABLE) as batch:
            batch.drop_column(COLUMN)
