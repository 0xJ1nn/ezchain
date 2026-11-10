"""Reconcile the ledger sequence index with the model on databases migrated before it.

Revision ID: 0017_ledger_seq_index
Revises: 0016_provider_keys

Revision 0010 originally created a unique constraint plus a plain index on
``ledger_entries.seq``, while the model declares one unique index. A database that already
ran that revision keeps the old shape, so autogenerate keeps reporting a difference.
Alembic stores no revision checksum, so correcting 0010 only helps a database created from
scratch; this revision brings an existing one to the same shape, guarded by catalog lookups
so it is a no-op on a database that is already correct or was created with the fixed 0010.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0017_ledger_seq_index"
down_revision: str | None = "0016_provider_keys"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

TABLE = "ledger_entries"
INDEX = "ix_ledger_entries_seq"
CONSTRAINT = "uq_ledger_entries_seq"


def _has_table(bind: Connection) -> bool:
    return TABLE in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind) or bind.dialect.name != "postgresql":
        # SQLite databases are built per test run and never reused; its one ALTER path
        # cannot drop a constraint without a table rebuild.
        return
    inspector = sa.inspect(bind)

    # The unique constraint and the unique index are the same rule twice. Keep the index
    # because that is what the model declares, and drop the constraint if it is there.
    names = {constraint["name"] for constraint in inspector.get_unique_constraints(TABLE)}
    if CONSTRAINT in names:
        op.drop_constraint(CONSTRAINT, TABLE, type_="unique")

    indexes = {index["name"]: index for index in sa.inspect(bind).get_indexes(TABLE)}
    current = indexes.get(INDEX)
    if current is None:
        op.create_index(INDEX, TABLE, ["seq"], unique=True)
    elif not current.get("unique"):
        op.drop_index(INDEX, table_name=TABLE)
        op.create_index(INDEX, TABLE, ["seq"], unique=True)


def downgrade() -> None:
    """Restore the shape revision 0010 used to leave, for a clean rollback."""
    bind = op.get_bind()
    if not _has_table(bind) or bind.dialect.name != "postgresql":
        return
    inspector = sa.inspect(bind)
    indexes = {index["name"]: index for index in inspector.get_indexes(TABLE)}
    current = indexes.get(INDEX)
    if current is not None and current.get("unique"):
        op.drop_index(INDEX, table_name=TABLE)
        op.create_index(INDEX, TABLE, ["seq"], unique=False)
    names = {constraint["name"] for constraint in inspector.get_unique_constraints(TABLE)}
    if CONSTRAINT not in names:
        op.create_unique_constraint(CONSTRAINT, TABLE, ["seq"])
