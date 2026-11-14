"""Give each strategy an optional trade-policy profile override.

Revision ID: 0023_strategy_risk_profile
Revises: 0022_strategy_creator

One nullable column on ``strategies``: ``risk_profile``. Null inherits the deployment
default, so a strategy written before this revision keeps exactly its current behaviour.
The value is the same ``low``/``mid``/``high`` name the order gate takes, never a second
copy of the limits.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_strategy_risk_profile"
down_revision: str | None = "0022_strategy_creator"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    from sqlalchemy import inspect

    inspector = inspect(op.get_bind())
    if "strategies" not in set(inspector.get_table_names()):
        return
    existing = {column["name"] for column in inspector.get_columns("strategies")}
    if "risk_profile" not in existing:
        op.add_column("strategies", sa.Column("risk_profile", sa.String(16), nullable=True))


def downgrade() -> None:
    from sqlalchemy import inspect

    inspector = inspect(op.get_bind())
    if "strategies" not in set(inspector.get_table_names()):
        return
    existing = {column["name"] for column in inspector.get_columns("strategies")}
    if "risk_profile" in existing:
        op.drop_column("strategies", "risk_profile")
