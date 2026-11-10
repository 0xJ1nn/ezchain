"""Paper wallets and per-chain settlement attribution.

Revision ID: 0015_paper_wallets
Revises: 0014_pair_identity

A wallet is a view over the journal, so this revision adds identity and attribution only:
a ``paper_wallets`` table, a nullable ``ledger_entries.wallet_id`` for the cash legs an
order settles, and the genesis settlement wallet those rows belong to. No balance is
stored and no existing ledger row is rewritten; a null attribution means the settlement
wallet, which is where the starting cash was booked before wallets existed.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0015_paper_wallets"
down_revision: str | None = "0014_pair_identity"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

SETTLEMENT_WALLET_ID = "paper-wallet:settlement"
LEDGER_WALLET_INDEX = "ix_ledger_entries_wallet_id"


def upgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "paper_wallets" not in tables:
        op.create_table(
            "paper_wallets",
            sa.Column("id", sa.String(80), primary_key=True),
            sa.Column("chain", sa.String(40), nullable=False),
            sa.Column("asset", sa.String(40), nullable=False),
            sa.Column("label", sa.String(120), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("chain", "asset", name="uq_paper_wallet_chain_asset"),
        )
        op.create_index("ix_paper_wallets_chain", "paper_wallets", ["chain"])
        op.create_index("ix_paper_wallets_created_at", "paper_wallets", ["created_at"])

    if "ledger_entries" in tables:
        columns = {column["name"] for column in inspector.get_columns("ledger_entries")}
        if "wallet_id" not in columns:
            op.add_column("ledger_entries", sa.Column("wallet_id", sa.String(80), nullable=True))
        indexes = {index["name"] for index in inspector.get_indexes("ledger_entries")}
        if LEDGER_WALLET_INDEX not in indexes:
            op.create_index(LEDGER_WALLET_INDEX, "ledger_entries", ["wallet_id"])

    # The genesis wallet must exist before any ledger row can name it. The chain wallets
    # are declared from the chain registry on boot, so this migration never duplicates it.
    exists = bind.execute(
        sa.text("SELECT 1 FROM paper_wallets WHERE id = :id"),
        {"id": SETTLEMENT_WALLET_ID},
    ).first()
    if exists is None:
        bind.execute(
            sa.text(
                "INSERT INTO paper_wallets (id, chain, asset, label, created_at) "
                "VALUES (:id, :chain, :asset, :label, :created_at)"
            ),
            {
                "id": SETTLEMENT_WALLET_ID,
                "chain": "settlement",
                "asset": "USD",
                "label": "Paper settlement",
                "created_at": datetime.now(UTC),
            },
        )


def downgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "ledger_entries" in tables:
        columns = {column["name"] for column in inspector.get_columns("ledger_entries")}
        indexes = {index["name"] for index in inspector.get_indexes("ledger_entries")}
        if LEDGER_WALLET_INDEX in indexes:
            op.drop_index(LEDGER_WALLET_INDEX, table_name="ledger_entries")
        if "wallet_id" in columns:
            op.drop_column("ledger_entries", "wallet_id")
    if "paper_wallets" in tables:
        op.drop_table("paper_wallets")
