"""Bind idempotency keys to the order request payload.

Existing rows keep a NULL fingerprint: they predate the column, so the order service
derives their hash from the persisted order on first reuse and writes it back. The
column stays nullable for that legacy case; every new order sets it at insert.

Revision ID: 0011_idempotency_fingerprint
Revises: 0010_ledger_sequence
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_idempotency_fingerprint"
down_revision: str | None = "0010_ledger_sequence"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    from sqlalchemy import inspect as sa_inspect

    bind = op.get_bind()
    if "paper_orders" not in sa_inspect(bind).get_table_names():
        return
    op.add_column(
        "paper_orders",
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    from sqlalchemy import inspect as sa_inspect

    bind = op.get_bind()
    if "paper_orders" not in sa_inspect(bind).get_table_names():
        return
    with op.batch_alter_table("paper_orders") as batch:
        batch.drop_column("request_fingerprint")
