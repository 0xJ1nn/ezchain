"""Tables for wallets the person follows and the swaps observed for them.

A tracked wallet is an identity, not an account: it holds no cash and no position.
Observed swaps are facts from an external source, keyed by that source's own
transaction id so a refresh is idempotent. Nothing here is a balance; holdings,
realized P&L and win rate are derived from the stored swaps at read time.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, DateTime, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ..models import MONEY, PRICE, QUANTITY, new_id, now

JSONType = JSON().with_variant(JSONB(), "postgresql")


class TrackedWalletRow(Base):
    __tablename__ = "tracked_wallets"
    __table_args__ = (UniqueConstraint("chain", "address", name="uq_tracked_wallet_chain_address"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    chain: Mapped[str] = mapped_column(String(40), index=True)
    address: Mapped[str] = mapped_column(String(160), index=True)
    label: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class WalletSwapRow(Base):
    __tablename__ = "wallet_swaps"
    __table_args__ = (
        UniqueConstraint("source", "source_tx", "token_address", name="uq_wallet_swap_observation"),
        Index("ix_wallet_swaps_wallet_at", "wallet_id", "observed_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    # An opaque wallet id, not a foreign key: untracking removes the identity, never
    # the observations already recorded against it.
    wallet_id: Mapped[str] = mapped_column(String(36), index=True)
    source: Mapped[str] = mapped_column(String(40))
    source_tx: Mapped[str] = mapped_column(String(160))
    chain: Mapped[str] = mapped_column(String(40))
    side: Mapped[str] = mapped_column(String(8))
    token_address: Mapped[str] = mapped_column(String(160), index=True)
    token_symbol: Mapped[str | None] = mapped_column(String(40), nullable=True)
    amount_tokens: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    amount_usd: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    price_usd: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
