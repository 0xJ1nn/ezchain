"""Persist tracked wallets and their observed swaps.

Revision ID: 0032_wallet_tracking
Revises: 0031_pair_address_width

A tracked wallet is an identity, not an account: it holds no cash and writes no
ledger entry. Observations are facts from a named source, keyed by that source's
transaction id and token so a refresh cannot double-count. Performance is derived
from these rows at read time; nothing here caches a P&L or a balance.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_wallet_tracking"
down_revision: str | None = "0031_pair_address_width"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

WALLETS = "tracked_wallets"
SWAPS = "wallet_swaps"


def upgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    existing = inspect(bind).get_table_names()
    if WALLETS not in existing:
        op.create_table(
            WALLETS,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("chain", sa.String(40), nullable=False),
            sa.Column("address", sa.String(160), nullable=False),
            sa.Column("label", sa.String(120), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("chain", "address", name="uq_tracked_wallet_chain_address"),
        )
        op.create_index("ix_tracked_wallets_chain", WALLETS, ["chain"])
        op.create_index("ix_tracked_wallets_address", WALLETS, ["address"])
        op.create_index("ix_tracked_wallets_created_at", WALLETS, ["created_at"])
    if SWAPS not in existing:
        op.create_table(
            SWAPS,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("wallet_id", sa.String(36), nullable=False),
            sa.Column("source", sa.String(40), nullable=False),
            sa.Column("source_tx", sa.String(160), nullable=False),
            sa.Column("chain", sa.String(40), nullable=False),
            sa.Column("side", sa.String(8), nullable=False),
            sa.Column("token_address", sa.String(160), nullable=False),
            sa.Column("token_symbol", sa.String(40), nullable=True),
            sa.Column("amount_tokens", sa.Numeric(38, 18), nullable=True),
            sa.Column("amount_usd", sa.Numeric(38, 8), nullable=True),
            sa.Column("price_usd", sa.Numeric(38, 18), nullable=True),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("raw", sa.JSON(), nullable=False),
            sa.UniqueConstraint(
                "source", "source_tx", "token_address", name="uq_wallet_swap_observation"
            ),
        )
        op.create_index("ix_wallet_swaps_wallet_id", SWAPS, ["wallet_id"])
        op.create_index("ix_wallet_swaps_token_address", SWAPS, ["token_address"])
        op.create_index("ix_wallet_swaps_observed_at", SWAPS, ["observed_at"])
        op.create_index("ix_wallet_swaps_wallet_at", SWAPS, ["wallet_id", "observed_at"])


def downgrade() -> None:
    from sqlalchemy import inspect

    existing = inspect(op.get_bind()).get_table_names()
    if SWAPS in existing:
        op.drop_table(SWAPS)
    if WALLETS in existing:
        op.drop_table(WALLETS)
