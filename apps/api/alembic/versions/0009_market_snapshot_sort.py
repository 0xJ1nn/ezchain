"""Index ``market_snapshots`` for the persisted browse sort.

Revision ID: 0009_market_snapshot_sort
Revises: 0008_api_keys
Create Date: 2026-10-23

Forward-only and additive: two indexes, no table rewrite and no backfill. The stored
browse reads ``ORDER BY liquidity_usd DESC NULLS LAST, observed_at DESC,
token_address``; without a matching index Postgres sorts every snapshot row before it can
apply the offset. The chain-prefixed variant serves a single-chain request, the
unprefixed one serves ``chain=all``.

SQLite rejects ``NULLS LAST`` inside ``CREATE INDEX``. The model indexes are therefore
Postgres-only and this migration is a no-op there: the API's SQLite path (including tests
and the desktop sidecar) keeps the same rows and order, scanned without these indexes.
The Postgres path is exercised against a fresh database by tools/check-migrations.sh.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009_market_snapshot_sort"
down_revision: str | None = "0008_api_keys"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_NULLS_LAST_OPS = {
    "liquidity_usd": "DESC NULLS LAST",
    "observed_at": "DESC",
    "token_address": "ASC",
}


def upgrade() -> None:
    # SQLite rejects ``NULLS LAST`` inside ``CREATE INDEX``, and the model's matching
    # indexes are conditional on Postgres for the same reason. The API's SQLite path
    # (including tests and the desktop sidecar) keeps the same rows and order, scanned
    # without this index.
    if op.get_bind().dialect.name == "sqlite":
        return
    op.create_index(
        "ix_market_snapshot_liquidity_observed",
        "market_snapshots",
        ["liquidity_usd", "observed_at", "token_address"],
        unique=False,
        postgresql_ops=dict(_NULLS_LAST_OPS),
    )
    op.create_index(
        "ix_market_snapshot_chain_liquidity_observed",
        "market_snapshots",
        ["chain", "liquidity_usd", "observed_at", "token_address"],
        unique=False,
        postgresql_ops=dict(_NULLS_LAST_OPS),
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    op.drop_index("ix_market_snapshot_chain_liquidity_observed", table_name="market_snapshots")
    op.drop_index("ix_market_snapshot_liquidity_observed", table_name="market_snapshots")
