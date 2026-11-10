"""Add encrypted, workspace-scoped provider credentials.

Revision ID: 0016_provider_keys
Revises: 0015_paper_wallets
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_provider_keys"
down_revision: str | None = "0015_paper_wallets"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_keys",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("secret_ciphertext", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_provider_keys_workspace_id"), "provider_keys", ["workspace_id"], unique=False
    )
    op.create_index(op.f("ix_provider_keys_provider"), "provider_keys", ["provider"], unique=False)
    op.create_index(
        op.f("ix_provider_keys_fingerprint"), "provider_keys", ["fingerprint"], unique=False
    )
    # One active key per workspace and provider. A second active row would be shadowed by
    # the newest on read and would silently become the active key again when that one is
    # revoked, so the database refuses it rather than trusting the service check alone.
    op.create_index(
        "uq_provider_keys_active",
        "provider_keys",
        ["workspace_id", "provider"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
        sqlite_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_provider_keys_active", table_name="provider_keys")
    op.drop_index(op.f("ix_provider_keys_fingerprint"), table_name="provider_keys")
    op.drop_index(op.f("ix_provider_keys_provider"), table_name="provider_keys")
    op.drop_index(op.f("ix_provider_keys_workspace_id"), table_name="provider_keys")
    op.drop_table("provider_keys")
