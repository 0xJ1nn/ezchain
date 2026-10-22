"""fill-order unique constraint for legacy databases

Revision ID: 0003_paper_fill_order
Revises: 0002_genesis_index
Create Date: 2026-10-22

Fresh databases get ``uq_paper_fill_order`` in the 0001 baseline. Databases created by
the old ``create_all()`` path may lack it; add it without failing on fresh installs.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_paper_fill_order"
down_revision: str | None = "0002_genesis_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_paper_fill_order'
            ) THEN
                ALTER TABLE paper_fills
                    ADD CONSTRAINT uq_paper_fill_order UNIQUE (order_id);
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE paper_fills DROP CONSTRAINT IF EXISTS uq_paper_fill_order")
