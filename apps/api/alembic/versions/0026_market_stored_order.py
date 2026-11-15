"""Index the stored market list's own order, so a page stops reading the table (GH-104).

Revision ID: 0026_market_stored_order
Revises: 0025_strategy_run_window

``GET /api/v1/markets/stored`` ordered the whole ``market_snapshots`` table in SQL and then
materialised every row into Python, where the filters and the page were applied. On a few
thousand tokens a page view read tens of megabytes, and no index could help: the ordering
began with ``liquidity_usd IS NULL`` and there was no index on the column at all. The only
index on the table, ``(chain, token_address)``, cannot serve an order on a column it does
not carry, so even a one-chain page sorted every row of that chain in a temporary B-tree.

The order is ``liquidity_usd IS NULL`` (unknown last), then ``liquidity_usd DESC``, then
``observed_at DESC`` for a stable order between two marks of equal liquidity, then ``id
DESC`` so two rows written in the same instant still have a total order for keyset paging.
The first key is the expression and not ``NULLS LAST`` because the two supported dialects
spell that differently and SQLite refuses it inside an index; the expression is the same
boolean every dialect can index and every dialect can order by.

Two indexes, because the leading key differs by request: a one-chain page seeks the
``chain``-leading one and never considers another chain's rows, and the all-chain page walks
the other. The cost is on write. Each upserted row now maintains two ordering entries on top
of the ``(chain, token_address)`` key it already maintained, so a market scan pays two index
inserts per changed token instead of one. Reads are the reason: an ordered page is a bounded
index walk of exactly ``limit`` entries, where it used to be a table read and a sort.

Both are expression indexes, which SQLAlchemy's inspector does not reflect on SQLite, so the
statements are ``IF NOT EXISTS``/``IF EXISTS`` rather than a catalog lookup: the dialect
decides whether the index is already there, and a re-run is a no-op either way.

Forward-only and additive: indexes are created, never a table or a column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_market_stored_order"
down_revision: str | None = "0025_strategy_run_window"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

TABLE = "market_snapshots"
ORDER = "((liquidity_usd IS NULL)), liquidity_usd DESC, observed_at DESC, id DESC"
INDEXES = {
    "ix_market_snapshot_liquidity_order": ORDER,
    "ix_market_snapshot_chain_liquidity_order": f"chain, {ORDER}",
}
COLUMNS = frozenset({"chain", "liquidity_usd", "observed_at", "id"})


def _table_has_the_columns() -> bool:
    """A hand-built or trimmed table has nothing to order, so there is nothing to index."""
    inspector = sa.inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        return False
    return COLUMNS <= {item["name"] for item in inspector.get_columns(TABLE)}


def upgrade() -> None:
    if not _table_has_the_columns():
        return
    for name, keys in INDEXES.items():
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {TABLE} ({keys})")


def downgrade() -> None:
    if not _table_has_the_columns():
        return
    for name in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
