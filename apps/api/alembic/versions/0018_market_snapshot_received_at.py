"""Keep the receiver's clock separate from the source's observation time.

Revision ID: 0018_market_snapshot_received_at
Revises: 0017_ledger_seq_index

A market observation carries the source's ``observed_at``, which a skewed or malicious
source can put in the future. The write clock is now stored alongside it as
``received_at``, so a rejected future-dated report stays auditable without its timestamp
being trusted for ordering. The column is nullable: rows written before it have no
receiver time, and freshness still reads the source timestamp.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_market_snapshot_received_at"
down_revision: str | None = "0017_ledger_seq_index"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    if "market_snapshots" not in inspect(bind).get_table_names():
        return
    op.add_column(
        "market_snapshots",
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    if "market_snapshots" not in inspect(bind).get_table_names():
        return
    op.drop_column("market_snapshots", "received_at")
