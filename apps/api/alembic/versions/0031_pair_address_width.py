"""Widen ``market_snapshots.pair_address`` so a Move pair fits (aptos).

Revision ID: 0031_pair_address_width
Revises: 0030_provider_keys_active_index

DexScreener names an aptos pair as two 66-character Move addresses joined by ``-``, which
is 200 characters. The column is ``VARCHAR(160)``, so every aptos snapshot failed to
insert with ``StringDataRightTruncation`` and the error was caught and logged by the market
monitor, which then simply had no aptos marks. Nothing surfaced it: the monitor logs a
warning, the API serves the rows it has, and the failing chain is invisible.

200 is not the widest. A pair name is ``<addressA>-<addressB>`` and this repository already
allows a 160-character token address in the same table, so the pair column has to hold two
of them plus the separator or it will fail again on the next family that spells a longer
address. It goes to 400 for that reason, not to 200 to fit today's aptos.

The three sibling columns (``base_token_address``, ``quote_token_address``,
``token_address``) stay at 160: each holds one address, and the widest one observed is 66.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_pair_address_width"
down_revision: str | None = "0030_provider_keys_active_index"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

TABLE = "market_snapshots"
COLUMN = "pair_address"
OLD, NEW = 160, 400


def upgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    # SQLite cannot ALTER a column type and does not enforce VARCHAR length either, so
    # there is nothing to do there — and a test that writes a 200-character pair against
    # SQLite on the old column would pass, which is why the regression test uses Postgres.
    if bind.dialect.name != "postgresql":
        return
    inspector = inspect(bind)
    if TABLE not in inspector.get_table_names():
        return
    columns = {column["name"]: column for column in inspector.get_columns(TABLE)}
    if COLUMN not in columns:
        return
    op.alter_column(
        TABLE,
        COLUMN,
        existing_type=sa.String(OLD),
        type_=sa.String(NEW),
        existing_nullable=True,
    )


def downgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    inspector = inspect(bind)
    if TABLE not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns(TABLE)}
    if COLUMN not in columns:
        return
    # A downgrade truncates: a pair address longer than the old width cannot be stored, and
    # Alembic's ALTER would raise rather than guess. Refuse with the count instead.
    long_rows = bind.execute(
        sa.text(f"SELECT COUNT(*) FROM {TABLE} WHERE LENGTH({COLUMN}) > {OLD}")
    ).scalar_one()
    if long_rows:
        raise RuntimeError(
            f"{long_rows} {TABLE} rows have a {COLUMN} longer than {OLD} characters; "
            "delete or shorten them before downgrading"
        )
    op.alter_column(
        TABLE,
        COLUMN,
        existing_type=sa.String(NEW),
        type_=sa.String(OLD),
        existing_nullable=True,
    )
