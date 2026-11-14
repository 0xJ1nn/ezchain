"""Record who created each strategy, so automation is attributed to its principal.

Revision ID: 0022_strategy_creator
Revises: 0021_approvals

Three nullable columns on ``strategies``: ``created_by_kind``, ``created_by_id`` and
``created_by_owner``. An order the engine places keeps its ``strategy`` attribution, but
its automation tier belongs to the principal that wrote the strategy. Nothing is
backfilled: a strategy written before this revision has no provenance, and the order gate
treats unknown provenance as automation (fail closed) rather than letting it run
unattended.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_strategy_creator"
down_revision: str | None = "0021_approvals"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

COLUMNS = {
    "created_by_kind": sa.String(16),
    "created_by_id": sa.String(120),
    "created_by_owner": sa.String(120),
}


def upgrade() -> None:
    from sqlalchemy import inspect

    inspector = inspect(op.get_bind())
    if "strategies" not in set(inspector.get_table_names()):
        return
    existing = {column["name"] for column in inspector.get_columns("strategies")}
    for name, type_ in COLUMNS.items():
        if name not in existing:
            op.add_column("strategies", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    from sqlalchemy import inspect

    inspector = inspect(op.get_bind())
    if "strategies" not in set(inspector.get_table_names()):
        return
    existing = {column["name"] for column in inspector.get_columns("strategies")}
    for name in COLUMNS:
        if name in existing:
            op.drop_column("strategies", name)
