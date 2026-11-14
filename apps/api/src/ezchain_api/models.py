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
    Delete,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    Update,
    event,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, ORMExecuteState, Session, mapped_column
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
    # The paper wallet this cash leg settles to. Null means the row predates wallets or
    # belongs to the genesis journal, which settles to the paper settlement wallet. The
    # ledger account is unchanged; this only attributes an existing cash row to a wallet.
    wallet_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    # The actor that placed the order this journal group belongs to, copied onto every
    # leg so the journal itself says who. The genesis journal and any rows written before
    # the columns existed carry no kind; they read as unknown.
    actor_kind: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class PaperWalletRow(Base):
    """A paper wallet: one settlement account per chain, plus the genesis wallet.

    A wallet is a view over the ledger, never a second accountant. The row declares which
    chain a wallet settles and in which asset; its balance is the sum of the ``cash:USD``
    ledger rows attributed to it. Nothing here caches a balance, and deleting a wallet
    cannot orphan a journal row because the attribution lives on the append-only entry.
    """

    __tablename__ = "paper_wallets"
    __table_args__ = (UniqueConstraint("chain", "asset", name="uq_paper_wallet_chain_asset"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=new_id)
    chain: Mapped[str] = mapped_column(String(40), index=True)
    asset: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(120))
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
    reasons: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType, nullable=True)
    quote_kind: Mapped[str | None] = mapped_column(String(24), nullable=True)
    quote_json: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    strategy_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # Who placed the order, decided on the server: ``human`` for the deployment token,
    # ``agent`` for a scoped key (addressed by its key id), ``strategy`` for an engine
    # order (addressed by its strategy id). A row written before these columns existed
    # has no kind, and ``unknown`` is how the read model reports it; it is never filled in
    # from a neighbouring field after the fact.
    actor_kind: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrderApprovalRow(Base):
    """An agent order held for a person's decision, and the record of that decision.

    Status is one declared lifecycle: ``pending`` (a person must approve or reject),
    ``approved`` (the decision claim, valid for exactly one execution attempt),
    ``rejected`` (a person refused it), ``expired`` (the five-minute window closed),
    ``superseded`` (a newer proposal replaced the same intent), ``executed`` (the stored
    order filled) and ``failed`` (the stored order was refused by the execution path).
    ``pending`` moves only to ``approved``, ``rejected``, ``expired`` or ``superseded``;
    ``approved`` moves only to ``executed`` or ``failed``.

    ``intent`` is the stored order request the approval authorises. The approve route
    executes it, never a body sent with the decision, so what a person approves is what
    the agent proposed. ``risk_decision`` holds the server's own pre-trade verdict and
    reason codes, never text from the proposing agent. ``decided_by_kind``/``decided_by_id``
    name the person who decided, resolved from the authenticated principal.
    """

    __tablename__ = "order_approvals"
    __table_args__ = (UniqueConstraint("order_id", name="uq_order_approval_order"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(ForeignKey("paper_orders.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    intent: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    risk_decision: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    execution_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    decided_by_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # The person's own words. Kept apart from ``risk_decision`` so a decision note can
    # never be mistaken for the server's risk verdict.
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AutomationSettingRow(Base):
    """One user's automation tier: ``manual`` (agent orders need a person) or ``full_auto``.

    The tier is per user and defaults to ``manual`` for a user with no row. A ``full_auto``
    row is only honoured while an append-only consent record for the same user and the
    current disclosure version exists; a missing or unrecognised tier reads as ``manual``,
    so an unreadable setting never lets an agent order through.
    """

    __tablename__ = "automation_settings"

    user_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    tier: Mapped[str] = mapped_column(String(16), default="manual")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_by_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    updated_by_id: Mapped[str | None] = mapped_column(String(120), nullable=True)


class AutomationConsentRow(Base):
    """An append-only record of a person accepting the full-auto risk disclosure.

    One row per opt-in. It names the user, the disclosure version and text that was
    accepted, and when. A consent is never updated or deleted; withdrawing full-auto
    writes a new ``manual`` setting and leaves this history intact.
    """

    __tablename__ = "automation_consents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(120), index=True)
    tier: Mapped[str] = mapped_column(String(16))
    disclosure_version: Mapped[str] = mapped_column(String(16))
    disclosure_text: Mapped[str] = mapped_column(Text)
    actor_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    acknowledged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, index=True
    )


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
    # The trade profile this strategy's orders carry. Null inherits the deployment default,
    # so a strategy written before this column keeps exactly its current behaviour. The
    # profile is a per-strategy override of the same policy the order gate applies; it is
    # never a second resolver.
    risk_profile: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # The principal that created the strategy, on the server. An order the engine places
    # for this strategy is attributed to the strategy, but its automation tier belongs to
    # the principal that wrote it: a strategy an agent created is an agent's automation.
    # Null means the provenance predates these columns; the order gate treats unknown
    # provenance as automation rather than letting it run unattended.
    created_by_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_by_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_by_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
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


class ProviderKeyRow(Base):
    """A user-supplied provider credential, encrypted before it reaches the database.

    The plaintext exists only between the request and the configured secret store. The
    row keeps the ciphertext, a non-reversible fingerprint for display, and the workspace
    that owns it. ``revoked_at`` is what retires a key: a revoked row is never resolved,
    and its ciphertext is cleared once the external copy is deleted. A failed deletion
    leaves the reference in place so the purge can be retried.

    One workspace holds at most one *active* key per provider. The partial unique index
    below is the rule's home: a resolver that picked the newest row would otherwise
    resurrect an older key the moment the newer one was revoked. Retired rows keep their
    history and are outside the index.
    """

    __tablename__ = "provider_keys"
    __table_args__ = (
        Index(
            "uq_provider_keys_active",
            "workspace_id",
            "provider",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
            sqlite_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    secret_ciphertext: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SystemFlagRow(Base):
    """Small persisted operator switches. Values are JSON so a flag can grow fields."""

    __tablename__ = "system_flags"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class MarketSnapshotRow(Base):
    """The latest observation per (chain, token), keyed by its unique index.

    ``market_snapshots.record_snapshot`` upserts on that key, so concurrent writers
    converge on one row. ``observed_at`` orders observations: a write never moves the
    stored mark backwards, and a timestamp ahead of the writer's clock never replaces one.
    """

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
    # When this process received the observation, kept apart from the source's own
    # ``observed_at`` so a skewed report is auditable without trusting its clock. Null on
    # rows written before the column existed; freshness reads the source timestamp.
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Pair identity carried over from the observation. Null when the source did not
    # report it; never inferred from the symbol or the token address.
    pair_address: Mapped[str | None] = mapped_column(String(160), nullable=True)
    dex: Mapped[str | None] = mapped_column(String(40), nullable=True)
    base_token_address: Mapped[str | None] = mapped_column(String(160), nullable=True)
    quote_token_address: Mapped[str | None] = mapped_column(String(160), nullable=True)
    pair_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
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


class MarketEventRow(Base):
    """One normalised market observation, as ``ezchain_core.MarketEvent`` writes it.

    The primary key is the contract's own idempotency identity
    ``(source name, pair, source timestamp)`` — the triple replay dedupes on — so a repeat
    of an observation cannot become a second row. Every key column is part of the key, which
    is also what lets a Timescale hypertable partition on ``source_observed_at`` without
    losing uniqueness.

    ``source_observed_at`` is when the *source* says the observation happened and is what
    retention ages on; ``observed_at`` is the ingest clock. Freshness is never stored: a
    reader derives age and staleness from the source timestamp against its own clock
    (CONTRACT I9).
    """

    __tablename__ = "market_events"
    __table_args__ = (
        Index("ix_market_events_pair_at", "pair", "source_observed_at"),
        Index("ix_market_events_chain_venue_at", "chain", "venue", "source_observed_at"),
    )

    source_name: Mapped[str] = mapped_column(String(120), primary_key=True)
    pair: Mapped[str] = mapped_column(String(160), primary_key=True)
    source_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    chain: Mapped[str] = mapped_column(String(40))
    venue: Mapped[str] = mapped_column(String(40))
    price: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    size: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    direction: Mapped[str | None] = mapped_column(String(4), nullable=True)
    units: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    schema_version: Mapped[int] = mapped_column(Integer)


class MarketOutboxRow(Base):
    """A market observation queued for downstream consumers, in the writing transaction.

    Written with the event row, so a consumer is never told about an observation that was
    rolled back and a committed observation always has its message. ``published_at`` is the
    relay's proof of delivery; an unpublished row is never pruned, because dropping it would
    lose evidence that committed.
    """

    __tablename__ = "market_outbox"
    __table_args__ = (
        Index(
            "ix_market_outbox_pending",
            "created_at",
            postgresql_where=text("published_at IS NULL"),
            sqlite_where=text("published_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    topic: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class AppendOnlyViolation(RuntimeError):
    """A caller tried to mutate or delete a protection audit row."""


def lock_protection_arm(session: Session, arm_id: str) -> None:
    """Serialize event appends for one arm before reading ``MAX(seq)``.

    Postgres takes a row lock on the arm. SQLite ignores ``FOR UPDATE``, so a
    no-op update there takes the database write lock instead. Without this,
    two transactions read the same maximum and the loser's unique-constraint
    failure rolls back its arm edit along with its rejected event.
    """
    if session.get_bind().dialect.name == "sqlite":
        session.execute(
            update(ProtectionArmRow)
            .where(ProtectionArmRow.id == arm_id)
            .values(id=ProtectionArmRow.id)
        )
    else:
        session.execute(
            select(ProtectionArmRow.id).where(ProtectionArmRow.id == arm_id).with_for_update()
        )


def next_protection_event_seq(session: Session, arm_id: str) -> int:
    """The next per-arm sequence, allocated while the arm row is locked."""
    lock_protection_arm(session, arm_id)
    return (
        int(
            session.execute(
                text("SELECT COALESCE(MAX(seq), 0) FROM protection_events WHERE arm_id = :arm_id"),
                {"arm_id": arm_id},
            ).scalar()
            or 0
        )
        + 1
    )


@event.listens_for(Session, "before_flush")
def _assign_protection_event_seqs(session: Session, _flush_context: Any, _instances: Any) -> None:
    """Assign a per-arm monotonic sequence before the rows reach the database.

    The ledger uses the same strategy: assign in Python so SQLite tests and
    Postgres agree, and so the order is stable inside one transaction. Rows
    that already carry a ``seq`` (the service allocates one under the arm lock)
    are left alone.
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
            highest[obj.arm_id] = next_protection_event_seq(session, obj.arm_id)
        else:
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


@event.listens_for(Session, "do_orm_execute")
def _reject_protection_event_bulk_mutations(state: ORMExecuteState) -> None:
    """Reject bulk ``UPDATE``/``DELETE`` statements aimed at the audit trail.

    ``session.execute(update(...))`` and ``session.execute(delete(...))`` do
    not populate ``session.dirty`` or ``session.deleted``, so the ``before_flush``
    guard above never sees them. The statement itself is the only signal.
    """
    statement = state.statement
    if not isinstance(statement, (Update, Delete)):
        return
    if statement.table.compare(ProtectionEventRow.__table__):
        verb = "updated" if isinstance(statement, Update) else "deleted"
        raise AppendOnlyViolation(f"protection events are append-only and cannot be {verb}")
