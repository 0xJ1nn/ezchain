"""Record who placed each order, on the order and on its journal group.

Revision ID: 0019_order_actor
Revises: 0018_market_snapshot_received_at

Two nullable columns on ``paper_orders`` and ``ledger_entries``: ``actor_kind`` names the
placing principal (``human``, ``agent`` or ``strategy``) and ``actor_id`` addresses it (the
operator subject, the agent key id, or the strategy id). Nothing is backfilled and no
existing row is rewritten: a row written before this revision has no kind, and every read
surface reports that as ``unknown`` rather than deriving an actor from a neighbouring
column. The columns stay nullable for exactly that legacy case; new rows always carry one.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_order_actor"
down_revision: str | None = "0018_market_snapshot_received_at"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

TARGETS = ("paper_orders", "ledger_entries")


def upgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    for table in TARGETS:
        if table not in tables:
            continue
        columns = {column["name"] for column in inspector.get_columns(table)}
        if "actor_kind" not in columns:
            op.add_column(table, sa.Column("actor_kind", sa.String(16), nullable=True))
        if "actor_id" not in columns:
            op.add_column(table, sa.Column("actor_id", sa.String(120), nullable=True))
        indexes = {index["name"] for index in inspector.get_indexes(table)}
        index_name = f"ix_{table}_actor_kind"
        if index_name not in indexes:
            op.create_index(index_name, table, ["actor_kind"])


def downgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    for table in TARGETS:
        if table not in tables:
            continue
        indexes = {index["name"] for index in inspector.get_indexes(table)}
        index_name = f"ix_{table}_actor_kind"
        if index_name in indexes:
            op.drop_index(index_name, table_name=table)
        columns = {column["name"] for column in inspector.get_columns(table)}
        with op.batch_alter_table(table) as batch:
            for column in ("actor_id", "actor_kind"):
                if column in columns:
                    batch.drop_column(column)
