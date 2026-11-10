"""SQLAlchemy models for the paper slice."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, Session, mapped_column
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
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True, index=True)
    group_id: Mapped[str] = mapped_column(String(36), index=True)
    account: Mapped[str] = mapped_column(String(160), index=True)
    asset: Mapped[str] = mapped_column(String(160))
    amount: Mapped[Decimal] = mapped_column(QUANTITY)
    usd_value: Mapped[Decimal] = mapped_column(MONEY)
    kind: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


@event.listens_for(Session, "before_flush")
def _assign_ledger_seqs(session: Session, _flush_context: Any, _instances: Any) -> None:
    """Assign monotonic sequences to new ledger rows before they are flushed.

    Postgres also has an IDENTITY default, but assigning in Python keeps SQLite-based
    tests working and guarantees a stable order within a single transaction. Rows
    that already carry a ``seq`` (backfilled or explicitly provided) are left alone.
    """
    pending = [
        obj
        for obj in session.new
        if isinstance(obj, LedgerEntryRow) and getattr(obj, "seq", None) is None
    ]
    if not pending:
        return
    query = text("SELECT COALESCE(MAX(seq), 0) FROM ledger_entries")
    current = int(session.execute(query).scalar() or 0)
    for obj in pending:
        current += 1
        obj.seq = current


class PaperOrderRow(Base):
    __tablename__ = "paper_orders"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_paper_order_idempotency"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    idempotency_key: Mapped[str] = mapped_column(String(120))
    # SHA-256 of the canonical request payload (chain, token, side, amount,
    # strategy). Binds the idempotency key to the order it represents, so a
    # replay with a different payload is detected instead of silently
    # returning the original order (CONTRACT I9).
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    # Pinned plugin identity. Null for the five built-in declarative kinds. The version
    # is the installed manifest version at creation time, not a floating lookup.
    plugin_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    plugin_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
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


class BacktestRow(Base):
    """An immutable backtest artifact: the request and the result it produced.

    ``strategy_id`` is an opaque historical string, not a foreign key: deleting a
    strategy must never delete or rewrite the measurements that were made from it.
    """

    __tablename__ = "backtests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    strategy_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    request: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class ApiKeyRow(Base):
    """A hashed, scoped agent credential.

    The plaintext token exists only in the creation response. ``token_hash`` is a
    SHA-256 of the token (optionally peppered); the row carries identity, scopes,
    expiry and audit timestamps. One deployment is one paper workspace; a key names
    the agent/user acting inside it.
    """

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    subject: Mapped[str] = mapped_column(String(120), index=True)
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scopes: Mapped[list[str]] = mapped_column(JSONType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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


class ProtectionArmRow(Base):
    """One manually armed protection order for an open paper position.

    Parameters live in ``params`` (validated by core and replayed from it).
    Trailing peaks and ladder progress are persisted here, so every trigger is
    computable from persisted inputs (CONTRACT I9). The close itself journals
    via the single paper order path; this table never holds money state.
    """

    __tablename__ = "protection_arms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    chain: Mapped[str] = mapped_column(String(40), index=True)
    token_address: Mapped[str] = mapped_column(String(160), index=True)
    token_symbol: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16), default="armed", index=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    peak_usd: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    triggered_levels: Mapped[list[int]] = mapped_column(JSONType, default=list)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    order_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class ProtectionProfileRow(Base):
    """A reusable protection preset. Chain null means the profile is global."""

    __tablename__ = "protection_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    chain: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(16))
    params: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    rules: Mapped[list[str]] = mapped_column(JSONType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class ProtectionEventRow(Base):
    """Append-only audit trail for one protection arm.

    One row per transition (armed, updated, hold reason, trigger, reject,
    cancel, expire). ``seq`` is monotonic within an arm, so the trail has a
    total order even when two observations share a timestamp. Rows are never
    updated or deleted; the ORM refuses both, and the HTTP surface has no
    mutation route. The arm's story is replayable from this table plus the arm
    row itself (CONTRACT I9).
    """

    __tablename__ = "protection_events"
    __table_args__ = (UniqueConstraint("arm_id", "seq", name="uq_protection_event_seq"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    arm_id: Mapped[str] = mapped_column(String(36), index=True)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(16), index=True)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rule_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    mark: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    order_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class AppendOnlyViolation(RuntimeError):
    """A caller tried to mutate or delete a protection audit row."""


@event.listens_for(Session, "before_flush")
def _assign_protection_event_seqs(session: Session, _flush_context: Any, _instances: Any) -> None:
    """Assign a per-arm monotonic sequence before the rows reach the database.

    The ledger uses the same strategy: assign in Python so SQLite tests and
    Postgres agree, and so the order is stable inside one transaction. Rows
    that already carry a ``seq`` are left alone.
    """
    pending = [
        obj
        for obj in session.new
        if isinstance(obj, ProtectionEventRow) and getattr(obj, "seq", None) is None
    ]
    if not pending:
        return
    highest: dict[str, int] = {}
    for obj in pending:
        if obj.arm_id not in highest:
            highest[obj.arm_id] = int(
                session.execute(
                    text(
                        "SELECT COALESCE(MAX(seq), 0) FROM protection_events WHERE arm_id = :arm_id"
                    ),
                    {"arm_id": obj.arm_id},
                ).scalar()
                or 0
            )
        highest[obj.arm_id] += 1
        obj.seq = highest[obj.arm_id]


@event.listens_for(Session, "before_flush")
def _reject_protection_event_mutations(
    session: Session, _flush_context: Any, _instances: Any
) -> None:
    """Keep the audit trail append-only at the ORM seam."""
    for obj in session.deleted:
        if isinstance(obj, ProtectionEventRow):
            raise AppendOnlyViolation("protection events are append-only and cannot be deleted")
    for obj in session.dirty:
        if isinstance(obj, ProtectionEventRow) and session.is_modified(obj):
            raise AppendOnlyViolation("protection events are append-only and cannot be updated")
