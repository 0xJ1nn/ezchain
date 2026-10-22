"""genesis single-writer index for legacy databases

Revision ID: 0002_genesis_index
Revises: 0001_baseline
Create Date: 2026-10-22

The baseline creates this index for fresh databases. A database created by the
pre-Alembic ``create_all()`` path was stamped at 0001 and therefore never received it;
this forward-only revision adds it idempotently. A fresh database runs 0001 then 0002,
and the ``IF NOT EXISTS`` keeps the second create harmless.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_genesis_index"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_ledger_genesis_account "
        "ON ledger_entries (group_id, account) "
        "WHERE group_id = 'genesis'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_ledger_genesis_account")
