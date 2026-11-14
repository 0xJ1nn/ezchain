"""An agent's orders draw from a capped bucket attributed on the ledger.

Revision ID: 0024_agent_buckets
Revises: 0022_strategy_creator

Two additive changes. ``agent_buckets`` holds one row per agent naming the most net cash
that agent may deploy and the owner who set it; an agent with no row has no bucket limit.
``ledger_entries.bucket_id`` attributes a cash leg to a bucket, so what an agent has
deployed is replayed from the journal (CONTRACT I1) rather than stored as a balance.

Nothing is backfilled and no existing row is rewritten: a leg written before this revision
has no bucket, and a bucket's deployment is exactly the sum of the legs that name it.
``cap_usd`` is NUMERIC(38, 8) on Postgres and TEXT on SQLite, matching every other money
column so an exact decimal round-trips on both.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_agent_buckets"
down_revision: str | None = "0023_strategy_risk_profile"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

LEDGER_BUCKET_INDEX = "ix_ledger_entries_bucket_id"


def upgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "agent_buckets" not in tables:
        op.create_table(
            "agent_buckets",
            sa.Column("agent_id", sa.String(120), primary_key=True),
            sa.Column("owner", sa.String(120), nullable=False),
            sa.Column("cap_usd", sa.Numeric(precision=38, scale=8), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_by_kind", sa.String(16), nullable=True),
            sa.Column("updated_by_id", sa.String(120), nullable=True),
        )
        op.create_index("ix_agent_buckets_owner", "agent_buckets", ["owner"])

    if "ledger_entries" in tables:
        columns = {column["name"] for column in inspector.get_columns("ledger_entries")}
        if "bucket_id" not in columns:
            op.add_column("ledger_entries", sa.Column("bucket_id", sa.String(120), nullable=True))
        indexes = {index["name"] for index in inspector.get_indexes("ledger_entries")}
        if LEDGER_BUCKET_INDEX not in indexes:
            op.create_index(LEDGER_BUCKET_INDEX, "ledger_entries", ["bucket_id"])


def downgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "ledger_entries" in tables:
        indexes = {index["name"] for index in inspector.get_indexes("ledger_entries")}
        columns = {column["name"] for column in inspector.get_columns("ledger_entries")}
        if LEDGER_BUCKET_INDEX in indexes:
            op.drop_index(LEDGER_BUCKET_INDEX, table_name="ledger_entries")
        if "bucket_id" in columns:
            op.drop_column("ledger_entries", "bucket_id")
    if "agent_buckets" in tables:
        op.drop_index("ix_agent_buckets_owner", table_name="agent_buckets")
        op.drop_table("agent_buckets")
