"""SQLAlchemy models for the paper slice."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeEngine

from .db import Base


class _ExactDecimal(TypeDecorator[Decimal]):
    """Exact decimal storage: NUMERIC on Postgres, TEXT on SQLite.

    SQLite has no native decimal type and would round-trip money through binary floats.
    Paper tests run on SQLite, so storing the decimal string keeps every fill and ledger
    entry exact and makes reconciliation assertions meaningful.
    """

    impl = Numeric
    cache_ok = True

    def __init__(self, precision: int, scale: int) -> None:
        super().__init__()
        self.precision = precision
        self.scale = scale

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(Text())
        return dialect.type_descriptor(Numeric(self.precision, self.scale))

    def process_bind_param(self, value: object, dialect: Dialect) -> object:
        if value is None:
            return None
        if dialect.name == "sqlite":
            return str(value)
        return Decimal(str(value))

    def process_result_value(self, value: object, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        return Decimal(str(value))


MONEY = _ExactDecimal(38, 8)
# Unit prices of long-tail tokens reach 1e-10 USD; eight decimals would round a mark (and
# every position value derived from it) by tens of percent. Prices keep eighteen.
PRICE = _ExactDecimal(38, 18)
QUANTITY = _ExactDecimal(38, 18)
JSONType = JSON().with_variant(JSONB(), "postgresql")


def new_id() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(UTC)


class LedgerEntryRow(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        # A genesis journal has exactly one cash and one equity leg. Two processes racing
        # on first boot must not both write a second genesis; the partial unique index
        # makes the loser fail its transaction instead of doubling the starting cash.
        Index(
            "uq_ledger_genesis_account",
            "group_id",
            "account",
            unique=True,
            postgresql_where=text("group_id = 'genesis'"),
            sqlite_where=text("group_id = 'genesis'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(String(36), index=True)
    account: Mapped[str] = mapped_column(String(160), index=True)
    asset: Mapped[str] = mapped_column(String(160))
    amount: Mapped[Decimal] = mapped_column(QUANTITY)
    usd_value: Mapped[Decimal] = mapped_column(MONEY)
    kind: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class PaperOrderRow(Base):
    __tablename__ = "paper_orders"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_paper_order_idempotency"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    idempotency_key: Mapped[str] = mapped_column(String(120))
    chain: Mapped[str] = mapped_column(String(40), index=True)
    token_address: Mapped[str] = mapped_column(String(160), index=True)
    side: Mapped[str] = mapped_column(String(8))
    amount_usd: Mapped[Decimal] = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    quote_kind: Mapped[str | None] = mapped_column(String(24), nullable=True)
    quote_json: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    strategy_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PaperFillRow(Base):
    __tablename__ = "paper_fills"
    __table_args__ = (UniqueConstraint("order_id", name="uq_paper_fill_order"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(ForeignKey("paper_orders.id"), index=True)
    chain: Mapped[str] = mapped_column(String(40), index=True)
    token_address: Mapped[str] = mapped_column(String(160), index=True)
    side: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[Decimal] = mapped_column(QUANTITY)
    price_usd: Mapped[Decimal] = mapped_column(PRICE)
    notional_usd: Mapped[Decimal] = mapped_column(MONEY)
    fee_usd: Mapped[Decimal] = mapped_column(MONEY)
    quote_kind: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class StrategyRow(Base):
    __tablename__ = "strategies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    chain: Mapped[str] = mapped_column(String(40), index=True)
    token_address: Mapped[str] = mapped_column(String(160), index=True)
    token_symbol: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(24))
    params: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StrategyRunRow(Base):
    __tablename__ = "strategy_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), index=True)
    action: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(64))
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    order_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class SystemFlagRow(Base):
    """Small persisted operator switches. Values are JSON so a flag can grow fields."""

    __tablename__ = "system_flags"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class MarketSnapshotRow(Base):
    __tablename__ = "market_snapshots"
    __table_args__ = (
        Index("ix_market_snapshot_chain_token", "chain", "token_address", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    chain: Mapped[str] = mapped_column(String(40))
    token_address: Mapped[str] = mapped_column(String(160))
    symbol: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(160))
    price_usd: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    liquidity_usd: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    volume_24h_usd: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    price_change_m5: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    price_change_h1: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    price_change_h6: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    price_change_h24: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    source: Mapped[str] = mapped_column(String(40))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class MarketCandleRow(Base):
    """One closed OHLCV bar per (chain, token, timeframe, open_time).

    History, not a snapshot: bars are additive and a re-fetch of the same bar updates it
    in place (the venue may revise a just-closed bar). Prices use the exact decimal type so
    an indicator never sees a float, and ``volume`` stays nullable because unknown is not
    zero. The forming bar is never stored — the service drops it before writing.
    """

    __tablename__ = "market_candles"
    __table_args__ = (
        UniqueConstraint(
            "chain",
            "token_address",
            "timeframe",
            "open_time",
            name="uq_market_candle_bar",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    chain: Mapped[str] = mapped_column(String(40))
    token_address: Mapped[str] = mapped_column(String(160))
    pool_address: Mapped[str] = mapped_column(String(160))
    timeframe: Mapped[str] = mapped_column(String(8))
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    open: Mapped[Decimal] = mapped_column(PRICE)
    high: Mapped[Decimal] = mapped_column(PRICE)
    low: Mapped[Decimal] = mapped_column(PRICE)
    close: Mapped[Decimal] = mapped_column(PRICE)
    volume: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    source: Mapped[str] = mapped_column(String(40))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
