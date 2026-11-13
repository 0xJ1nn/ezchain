"""Persist normalised market events with a transactional outbox (GH-26).

Revision ID: 0020_market_events
Revises: 0019_order_actor

Forward-only and additive: two new tables, no backfill and no change to an existing one.

``market_events`` is one row per ``ezchain_core.MarketEvent``, keyed by the contract's own
idempotency identity ``(source_name, pair, source_observed_at)``. Nothing about the shape is
invented here: the event log's replay already dedupes on that triple, so the schema and the
replay agree by construction and a retried write cannot create a second observation.

``market_outbox`` is the downstream hook from ADR-0002. A writer appends one row in the same
transaction as the event insert, so a consumer is never told about an observation that was
rolled back, and a committed observation always has its message.

**Timescale is used when the server has it, never required.** On plain PostgreSQL the tables
are ordinary tables and every guarantee above holds — which is the deployment
``infra/compose.yaml`` actually ships (``postgres:16-alpine``), so a migration that insisted
on the extension would refuse to apply there (CONTRACT I8: a fresh database must be creatable
from ``main``). When the ``timescaledb`` extension is available, ``market_events`` is
additionally converted into a hypertable partitioned on ``source_observed_at``. That is
legal because the partition column is already part of the primary key; a hypertable requires
every unique index to contain it.

``market_candles`` is deliberately **not** converted. Its primary key is a surrogate ``id``
that does not contain ``open_time``, so Timescale would refuse the conversion, and rewriting
that key is a destructive change to a table this issue does not own. The raw window is
enforced for both plain PostgreSQL and Timescale by ``ezchain_ingest.retention.prune``, which
reads one validated configuration (CONTRACT I7), rather than by a database policy that would
duplicate the numbers.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_market_events"
down_revision: str | None = "0019_order_actor"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _has_timescaledb(bind: sa.engine.Connection) -> bool:
    """True when the server offers a Timescale extension this migration may use."""
    if bind.dialect.name != "postgresql":
        return False
    return (
        bind.execute(
            sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
        ).first()
        is not None
    )


def upgrade() -> None:
    op.create_table(
        "market_events",
        sa.Column("source_name", sa.String(length=120), nullable=False),
        sa.Column("pair", sa.String(length=160), nullable=False),
        sa.Column("source_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("chain", sa.String(length=40), nullable=False),
        sa.Column("venue", sa.String(length=40), nullable=False),
        sa.Column("price", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("size", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("direction", sa.String(length=4), nullable=True),
        sa.Column("units", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint(
            "source_name", "pair", "source_observed_at", name="pk_market_events"
        ),
    )
    op.create_index("ix_market_events_pair_at", "market_events", ["pair", "source_observed_at"])
    op.create_index(
        "ix_market_events_chain_venue_at",
        "market_events",
        ["chain", "venue", "source_observed_at"],
    )

    op.create_table(
        "market_outbox",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("topic", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("id", name="pk_market_outbox"),
    )
    op.create_index(
        "ix_market_outbox_pending",
        "market_outbox",
        ["created_at"],
        postgresql_where=sa.text("published_at IS NULL"),
        sqlite_where=sa.text("published_at IS NULL"),
    )

    bind = op.get_bind()
    if not _has_timescaledb(bind):
        return
    # Optional additive use of an installed extension. ``if_not_exists`` keeps a re-run and
    # a pre-provisioned database on the same path.
    bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
    bind.execute(
        sa.text(
            "SELECT create_hypertable("
            "'market_events', 'source_observed_at', "
            "if_not_exists => TRUE, migrate_data => TRUE)"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_market_outbox_pending", table_name="market_outbox")
    op.drop_table("market_outbox")
    op.drop_index("ix_market_events_chain_venue_at", table_name="market_events")
    op.drop_index("ix_market_events_pair_at", table_name="market_events")
    op.drop_table("market_events")
