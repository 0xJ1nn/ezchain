"""Restore the active-key index on a database stamped at 0016 without it (GH-218).

Revision ID: 0030_provider_keys_active_index
Revises: 0029_watchlist

A developer who stamped an intermediate commit of the provider-keys branch could carry
the 0016 stamp while the partial unique index was never created. A later ``upgrade head``
skips 0016 entirely, leaving the model's "one active key per workspace and provider"
guarantee unenforced. This additive revision checks the catalog and creates the index
only when it is absent; a database that already has it is untouched.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_provider_keys_active_index"
down_revision: str | None = "0029_watchlist"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

TABLE = "provider_keys"
INDEX = "uq_provider_keys_active"
WHERE = sa.text("revoked_at IS NULL")


def _table_exists() -> bool:
    from sqlalchemy import inspect

    return TABLE in inspect(op.get_bind()).get_table_names()


def _indexes() -> set[str]:
    from sqlalchemy import inspect

    return {index["name"] for index in inspect(op.get_bind()).get_indexes(TABLE) if index["name"]}


def upgrade() -> None:
    if not _table_exists() or INDEX in _indexes():
        return
    op.create_index(
        INDEX,
        TABLE,
        ["workspace_id", "provider"],
        unique=True,
        postgresql_where=WHERE,
        sqlite_where=WHERE,
    )


def downgrade() -> None:
    if _table_exists() and INDEX in _indexes():
        op.drop_index(INDEX, table_name=TABLE)
