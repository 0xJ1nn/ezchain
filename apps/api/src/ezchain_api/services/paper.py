"""Paper order service and ledger-derived read models.

The ledger is the only accountant (CONTRACT I1). An order is claimed by its unique
idempotency key, risk is evaluated before any fill, and the order, fill and ledger journal
commit in one transaction. Cash, positions and PnL are projected by replaying ledger
entries, never read from a mutable scalar.

Every sell is bounded by the held quantity and can never open a short. A reduce-only exit
— a sell explicitly flagged ``reduce_only``, or one whose requested size clamps to the
whole position — bypasses entry-only gates: its executable sell quote is the exit's
price, so a missing or stale market mark may not trap the position. Settlement routing is
an entry gate too: a sell settles to its chain's wallet when that wallet is valid and to
the settlement wallet otherwise, so a missing or wrong-asset wallet cannot trap an exit
either. Exit integrity still fails closed: a held position, a fresh quote, an executable
route and a quantity no larger than the position are all required, and a sell with no
route is persisted as ``EXIT_NO_ROUTE`` with its quote evidence.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ezchain_adapters import CHAINS, AdapterError, DexScreenerClient, NoRoute, QuoteRouter
from ezchain_core import (
    MARK_FUTURE_SKEW_SECONDS,
    TRADE_RISK_PROFILES,
    CostPreview,
    FreshnessPolicy,
    LedgerEntry,
    MarkStatus,
    OrderRequest,
    PositionView,
    Quote,
    RiskLimits,
    RiskReason,
    SourceRef,
    TokenSnapshot,
    TradeRiskProfile,
    evaluate_order,
    genesis_entries,
    preview_cost,
    replay_position,
    utcnow,
)
from ezchain_core.ledger import (
    buy_entries,
    cash_account,
    position_account,
    sell_entries,
)
from ezchain_core.types import RISK_REASON_MESSAGES

from ..actor import ACTOR_STRATEGY, ACTOR_UNKNOWN, UNKNOWN_ACTOR, OrderActor
from ..config import Settings, get_settings
from ..models import (
    LedgerEntryRow,
    MarketSnapshotRow,
    PaperFillRow,
    PaperOrderRow,
    PaperWalletRow,
    new_id,
)
from . import market_snapshots, pnl
from .system_flags import read_kill_switch

GENESIS_GROUP = "genesis"
# Per-request flag recording whether the last ``place_order`` call created a new
# order row (True) or replayed an existing one (False). A context variable keeps
# it correct under concurrent async requests.
_ORDER_CREATED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "paper_order_created", default=False
)


def last_created() -> bool:
    """Whether the most recent place_order call created a new order."""
    return _ORDER_CREATED.get()


def resolve_actor(payload: OrderRequest, actor: OrderActor | None) -> OrderActor:
    """The placing principal for one order, from facts the server holds.

    Strategy attribution wins: the engine is the only caller that sets ``strategy_id`` (the
    manual and MCP routes refuse the field), so an order that carries one was placed by
    that strategy no matter what a caller passed. Otherwise the API edge supplies the
    authenticated principal, and a caller with no principal is recorded as unknown.
    """
    if payload.strategy_id is not None:
        return OrderActor(ACTOR_STRATEGY, payload.strategy_id)
    if actor is None or actor.kind == ACTOR_UNKNOWN:
        return UNKNOWN_ACTOR
    return actor


POSITION_PREFIX = "position:"
MONEY_QUANTUM = Decimal("0.00000001")
SHARE_QUANTUM = Decimal("0.01")
# The ledger sums money exactly. Python's default decimal context carries 28 significant
# digits, which is a display precision: SQLite stores money as TEXT and keeps the
# unrounded value of a division, so a few hundred rows can need more digits than that.
# Summing cash and its wallet buckets at this precision makes the buckets an exact
# partition of cash, independent of order (CONTRACT I1).
LEDGER_PRECISION = 60
SETTLEMENT_CHAIN = "settlement"
# How far an observation may run ahead of the reader's clock before it stops being a
# tolerated skew and becomes an unusable (unknown) mark. The bound lives in core so the
# writer, this read model and the risk gate share one value; a far-future legacy row is
# reported unknown rather than "future", so it cannot freeze a position's price.
FUTURE_SKEW_SECONDS = MARK_FUTURE_SKEW_SECONDS
SETTLEMENT_ASSET = "USD"
# Deterministic ids keep the genesis wallet stable across a migration, a restart and a
# second process. A wallet is addressed by its chain, which is unique per asset.
SETTLEMENT_WALLET_ID = "paper-wallet:settlement"
WALLET_PREFIX = "paper-wallet:"
# The read-model bucket for cash whose attributed wallet id has no row. It is not a
# wallet: it exists so the wallet list stays a complete partition of the cash account.
UNALLOCATED_CHAIN = "unallocated"
UNALLOCATED_LABEL = "Unallocated paper cash"


# ---------------------------------------------------------------------------
# Genesis and replay
# ---------------------------------------------------------------------------


def ensure_genesis(session: Session, starting_cash_usd: Decimal) -> None:
    """Create the genesis journal exactly once, keyed by its group id.

    The paper wallets are seeded first, so the starting cash can be attributed to the
    genesis wallet in the same journal that creates it. A wallet is a view over the
    ledger: the cash account and every existing read model keep working unchanged.
    """
    ensure_wallets(session)
    existing = session.scalar(
        select(LedgerEntryRow.id).where(LedgerEntryRow.group_id == GENESIS_GROUP).limit(1)
    )
    if existing is not None:
        return
    for entry in genesis_entries(starting_cash_usd):
        row = entry.to_row(GENESIS_GROUP)
        if row["account"] == cash_account():
            row["wallet_id"] = SETTLEMENT_WALLET_ID
        session.add(LedgerEntryRow(**row))
    try:
        session.commit()
    except IntegrityError:
        # A concurrent first boot won the partial unique index. Its genesis is the
        # only one; this transaction wrote nothing. Re-read to prove it exists rather
        # than swallowing an unrelated integrity failure.
        session.rollback()
        if (
            session.scalar(
                select(LedgerEntryRow.id).where(LedgerEntryRow.group_id == GENESIS_GROUP).limit(1)
            )
            is None
        ):
            raise


# ---------------------------------------------------------------------------
# Paper wallets
# ---------------------------------------------------------------------------


def _seeded_wallets() -> list[tuple[str, str, str, str]]:
    """The wallets this deployment declares: one settlement wallet per capable chain.

    The registry owns the chain list, so adding a chain adds its wallet on the next boot
    without a migration. Each wallet settles in the ledger's settlement asset; a wallet
    that declares anything else is refused at the order edge, never silently coerced.
    """
    rows = [(SETTLEMENT_WALLET_ID, SETTLEMENT_CHAIN, SETTLEMENT_ASSET, "Paper settlement")]
    for meta in sorted(CHAINS, key=lambda chain: chain.id):
        if meta.paper_execute:
            rows.append(
                (
                    f"{WALLET_PREFIX}{meta.id}",
                    meta.id,
                    SETTLEMENT_ASSET,
                    f"{meta.name} paper wallet",
                )
            )
    return rows


def ensure_wallets(session: Session) -> None:
    """Insert any declared wallet that is missing, exactly once.

    Idempotent and safe to call on every boot and every order. A concurrent insert loses
    the unique constraint and re-reads rather than failing a request; an unrelated
    integrity error is re-raised. Reads never call this: declaring a wallet writes no
    money, but a GET must not write at all.
    """
    existing = set(session.scalars(select(PaperWalletRow.id)))
    pending = [row for row in _seeded_wallets() if row[0] not in existing]
    if not pending:
        return
    for wallet_id, chain, asset, label in pending:
        session.add(PaperWalletRow(id=wallet_id, chain=chain, asset=asset, label=label))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = set(session.scalars(select(PaperWalletRow.id)))
        if any(row[0] not in existing for row in pending):
            raise


def wallet_for_chain(session: Session, chain: str) -> tuple[PaperWalletRow | None, str | None]:
    """The wallet that settles an order on ``chain``, or a typed refusal.

    Returns ``(wallet, None)`` when a wallet settles in the ledger's asset. An unknown
    chain, and a wallet declaring a different settlement asset, are both refusals with
    their own reason code: an unattributable settlement is never guessed.
    """
    rows = list(session.scalars(select(PaperWalletRow).where(PaperWalletRow.chain == chain)))
    if not rows:
        return None, "WALLET_UNKNOWN"
    for row in rows:
        if row.asset == SETTLEMENT_ASSET:
            return row, None
    return rows[0], "SETTLEMENT_ASSET_UNKNOWN"


@dataclass(frozen=True)
class WalletView:
    """One paper wallet's identity and ledger-derived settlement balance.

    ``id`` is null for the ``unallocated`` bucket: that row is not a wallet, it keeps
    cash attributed to a wallet id whose row no longer exists.
    """

    id: str | None
    chain: str
    asset: str
    label: str
    balance_usd: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "chain": self.chain,
            "asset": self.asset,
            "label": self.label,
            "balance_usd": str(self.balance_usd),
            "mode": "paper",
        }


def wallet_views(session: Session, replay: Replay | None = None) -> list[WalletView]:
    """Every declared wallet plus an unallocated bucket, with ledger-derived balances.

    Balances come from ``Replay.wallet_cash``: the same Python ``Decimal`` pass that
    computes the account's cash, never a SQL aggregate. SQLite stores money as TEXT, so
    ``SUM()`` would coerce every row to a float and the wallets would no longer add up to
    the account to the last unit (CONTRACT I1). A caller that already replayed the ledger
    passes its ``Replay`` so one request reads the journal once.

    The returned rows are a complete partition of the cash account. Cash attributed to a
    wallet id whose row is gone is reported in one labelled ``unallocated`` row with a
    null id, so ``sum(balance_usd)`` still equals ``cash_usd`` instead of silently
    dropping money. The bucket is a read model, never a wallet.
    """
    rows = list(session.scalars(select(PaperWalletRow)))
    rows.sort(key=lambda row: (row.chain != SETTLEMENT_CHAIN, row.chain))
    balances = (replay if replay is not None else _replay(_ledger_rows(session))).wallet_cash
    views = [
        WalletView(
            id=row.id,
            chain=row.chain,
            asset=row.asset,
            label=row.label,
            balance_usd=balances.get(row.id, Decimal(0)),
        )
        for row in rows
    ]
    declared = {row.id for row in rows}
    with localcontext() as context:
        context.prec = LEDGER_PRECISION
        unattributed = sum(
            (balance for wallet_id, balance in balances.items() if wallet_id not in declared),
            Decimal(0),
        )
    if unattributed != 0:
        views.append(
            WalletView(
                id=None,
                chain=UNALLOCATED_CHAIN,
                asset=SETTLEMENT_ASSET,
                label=UNALLOCATED_LABEL,
                balance_usd=unattributed,
            )
        )
    return views


def wallets(session: Session) -> list[dict[str, Any]]:
    """The paper wallets as API rows, settlement first and an unallocated bucket last."""
    return [view.to_dict() for view in wallet_views(session)]


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _ledger_rows(session: Session) -> list[LedgerEntryRow]:
    return list(session.scalars(select(LedgerEntryRow).order_by(LedgerEntryRow.seq)))


def _domain_entry(row: LedgerEntryRow) -> LedgerEntry:
    return LedgerEntry(
        account=row.account,
        asset=row.asset,
        amount=row.amount,
        usd_value=row.usd_value,
        kind=row.kind,
    )


@dataclass(frozen=True)
class Replay:
    cash: Decimal
    realized: Decimal
    daily_realized: Decimal
    positions: dict[str, tuple[Decimal, Decimal]]
    realized_by_account: dict[str, Decimal]
    # Cash per settlement wallet, bucketed in the same pass as ``cash``. A row without
    # an attribution (written before wallets existed) belongs to the genesis wallet.
    wallet_cash: dict[str, Decimal]


def group_position_delta(rows: list[LedgerEntryRow]) -> Decimal:
    """The trade result of one order group: its cash delta plus its position delta.

    A group without a position leg (genesis) is not a trade and therefore contributes
    nothing. A buy group's result is ``-fee``; a sell group's result is
    ``proceeds - cost_basis - fee``. This is additive across groups, so it does not depend
    on replay order and reconciles with cash plus the cost basis of open positions.
    """
    position_rows = [row for row in rows if row.account.startswith(POSITION_PREFIX)]
    if not position_rows:
        return Decimal(0)
    cash_delta = sum((row.usd_value for row in rows if row.account == cash_account()), Decimal(0))
    return cash_delta + sum((row.usd_value for row in position_rows), Decimal(0))


def ledger_rows(session: Session) -> list[LedgerEntryRow]:
    """Every ledger entry in journal order (oldest first). Public read-only access."""
    return _ledger_rows(session)


def project_ledger(rows: list[LedgerEntryRow], *, day_start: datetime | None = None) -> Replay:
    """Project cash, positions and realized PnL from ledger rows (CONTRACT I1)."""
    return _replay(rows, day_start=day_start)


def _replay(rows: list[LedgerEntryRow], *, day_start: datetime | None = None) -> Replay:
    """Project balances, positions and realized PnL from ledger rows.

    Per-position net realized is derived per order group: a group's cash deltas plus its
    position delta equal the trade's result after fees (negative fees on a buy). This is
    additive, so it does not depend on replay order and reconciles with cash + mark.

    ``cash`` and ``wallet_cash`` are accumulated in one pass at ``LEDGER_PRECISION`` so the
    wallet buckets are an exact partition of the cash account: the wallet rows add up to
    the account figure to the last digit, and no SQL aggregate ever re-sums them.
    """
    cash = Decimal(0)
    wallet_cash: dict[str, Decimal] = {}
    groups: dict[str, list[LedgerEntryRow]] = {}
    position_entries: dict[str, list[LedgerEntry]] = {}

    with localcontext() as context:
        context.prec = LEDGER_PRECISION
        for row in rows:
            if row.account == cash_account():
                cash += row.usd_value
                wallet = row.wallet_id or SETTLEMENT_WALLET_ID
                wallet_cash[wallet] = wallet_cash.get(wallet, Decimal(0)) + row.usd_value
            groups.setdefault(row.group_id, []).append(row)
            if row.account.startswith(POSITION_PREFIX):
                position_entries.setdefault(row.account, []).append(_domain_entry(row))

    positions = {account: replay_position(entries) for account, entries in position_entries.items()}

    realized_by_account: dict[str, Decimal] = {}
    daily = Decimal(0)
    for group in groups.values():
        position_rows = [row for row in group if row.account.startswith(POSITION_PREFIX)]
        if not position_rows:
            continue
        delta = group_position_delta(group)
        for row in position_rows:
            realized_by_account[row.account] = (
                realized_by_account.get(row.account, Decimal(0)) + delta
            )
        if day_start is not None:
            stamps = [_aware(row.created_at) for row in group]
            observed = min((stamp for stamp in stamps if stamp is not None), default=None)
            if observed is not None and observed >= day_start:
                daily += delta

    return Replay(
        cash=cash,
        realized=sum(realized_by_account.values(), Decimal(0)),
        daily_realized=daily,
        positions=positions,
        realized_by_account=realized_by_account,
        wallet_cash=wallet_cash,
    )


def _open_positions(replay: Replay) -> int:
    return sum(1 for quantity, _ in replay.positions.values() if quantity > 0)


def _split_position_account(account: str) -> tuple[str, str]:
    chain, _, token = account[len(POSITION_PREFIX) :].partition(":")
    return chain, token


def _day_start() -> datetime:
    now = utcnow()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Market snapshots
# ---------------------------------------------------------------------------


def record_snapshot(session: Session, snapshot: TokenSnapshot) -> MarketSnapshotRow | None:
    """Upsert the latest observation for (chain, token); older events never overwrite.

    A report dated beyond the tolerated skew is rejected (``MARK_TIMESTAMP_FUTURE``) and
    returns ``None``; it never becomes a mark and never suppresses a later correct one.
    """
    return market_snapshots.record_snapshot(session, snapshot)


def _snapshot_index(session: Session) -> dict[tuple[str, str], MarketSnapshotRow]:
    rows = session.scalars(select(MarketSnapshotRow).order_by(MarketSnapshotRow.observed_at)).all()
    return {(row.chain, row.token_address): row for row in rows}


def _source_ref(row: MarketSnapshotRow) -> SourceRef:
    observed = _aware(row.observed_at) or utcnow()
    return SourceRef(name=row.source, url=None, observed_at=observed)


def _mark_freshness(
    mark_usd: Any, source: Any, max_age_seconds: float, now: datetime | None = None
) -> tuple[MarkStatus, int | None]:
    """Typed freshness of one mark and its age in whole seconds.

    The age is null whenever the status is ``unknown``: there is no observation to age, and
    reporting a number there would let a client print a fresh-looking mark that is not one.
    """
    status = _mark_status(mark_usd, source, max_age_seconds, now)
    if status == "unknown" or not isinstance(source, dict):
        return status, None
    observed_raw = source.get("observed_at")
    if not isinstance(observed_raw, str):
        return status, None
    try:
        stamp = _aware(datetime.fromisoformat(observed_raw))
    except ValueError:
        return status, None
    if stamp is None:
        return status, None
    return status, max(int(((now or utcnow()) - stamp).total_seconds()), 0)


def _mark_status(
    mark_usd: Any, source: Any, max_age_seconds: float, now: datetime | None = None
) -> MarkStatus:
    """Typed freshness of one mark, separate from any quote's freshness.

    The classification lives in :func:`ezchain_api.services.pnl.mark_status` so the
    order gate, the account read and the performance/attribution reads cannot drift
    into different verdicts for the same observation.
    """
    observed: datetime | None = None
    if isinstance(source, dict):
        observed_raw = source.get("observed_at")
        if isinstance(observed_raw, str):
            try:
                observed = _aware(datetime.fromisoformat(observed_raw))
            except ValueError:
                observed = None
    return pnl.mark_status(mark_usd, observed, max_age_seconds=max_age_seconds, now=now)


def _mark_can_price(
    snapshot: TokenSnapshot, max_age_seconds: float, now: datetime | None = None
) -> bool:
    """Whether a mark may price at all: positive, sourced and fresh or tolerated future.

    ``stale`` and ``unknown`` are not usable. An exit never asks this to choose its size;
    it only decides whether a *normal* sell can convert its USD notional at the mark.
    """
    if snapshot.price_usd is None or snapshot.price_usd <= 0 or snapshot.source is None:
        return False
    status = _mark_status(snapshot.price_usd, snapshot.source.to_dict(), max_age_seconds, now)
    return status in ("fresh", "future")


# ---------------------------------------------------------------------------
# Read models
# ---------------------------------------------------------------------------


def _position_views(
    replay: Replay,
    snapshots: dict[tuple[str, str], MarketSnapshotRow],
    *,
    max_age_seconds: float = 60.0,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    views: list[dict[str, Any]] = []
    now = now or utcnow()
    for account, (quantity, cost) in replay.positions.items():
        if quantity <= 0:
            continue
        chain, token = _split_position_account(account)
        snapshot = snapshots.get((chain, token))
        mark = snapshot.price_usd if snapshot is not None else None
        # A source describes where a mark came from. A snapshot row can exist with no
        # price (a failed observation); labelling that row with a source would present
        # it as priced and leak an observation age to dashboard/positions/reports.
        source = None if snapshot is None or mark is None else _source_ref(snapshot)
        status, age_seconds = _mark_freshness(
            mark, None if source is None else source.to_dict(), max_age_seconds, now
        )
        # ``unknown`` fails closed: a mark whose freshness cannot be trusted (a legacy
        # far-future observation, an unparsable time) prices nothing. The same holds for
        # ``stale``: an old mark is a label, not a live number. Keeping the price would
        # let equity, composition and unrealized PnL be computed from an old or frozen
        # number while the status said the mark is unusable (CONTRACT I3). The source is
        # not a money number and stays, so the alert feed can still name the source.
        priced = mark is not None and status not in ("stale", "unknown")
        mark_usd = mark if priced else None
        market_value = None if mark_usd is None else quantity * mark_usd
        unrealized = None if market_value is None else market_value - cost
        view = PositionView(
            chain=chain,
            token_address=token,
            symbol=snapshot.symbol if snapshot is not None and snapshot.symbol else token,
            quantity=quantity,
            avg_cost_usd=cost / quantity,
            mark_usd=mark_usd,
            market_value_usd=market_value,
            unrealized_usd=unrealized,
            realized_usd=replay.realized_by_account.get(account, Decimal(0)),
            source=source,
            mark_status=status,
            mark_age_seconds=age_seconds,
            mode="paper",
        )
        views.append(view.to_dict())
    views.sort(key=lambda item: (item["chain"], item["token_address"]))
    return views


def positions(session: Session, *, max_age_seconds: float | None = None) -> list[dict[str, Any]]:
    replay = _replay(_ledger_rows(session))
    snapshots = _snapshot_index(session)
    if max_age_seconds is None:
        return _position_views(replay, snapshots)
    return _position_views(replay, snapshots, max_age_seconds=max_age_seconds)


def _share_pct(value: Decimal, equity: Decimal) -> str | None:
    """A row's share of equity, computed only when the equity total is usable."""
    if equity <= 0:
        return None
    return str((value * 100 / equity).quantize(SHARE_QUANTUM))


def _chain_mark_status(members: list[dict[str, Any]]) -> str:
    statuses = [str(member.get("mark_status", "unknown")) for member in members]
    if any(status == "unknown" for status in statuses):
        return "unknown"
    if any(status == "future" for status in statuses):
        return "future"
    if any(status == "stale" for status in statuses):
        return "stale"
    return "fresh"


def _composition(
    views: list[dict[str, Any]],
    cash: Decimal,
    *,
    complete: bool,
    equity: Decimal,
    max_age_seconds: float = 60.0,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Per-chain and per-asset composition, computed once on the server.

    An asset without a mark keeps its row with ``market_value_usd`` and ``share_pct`` null:
    it never disappears and is never valued at zero. A chain with any unpriced asset also
    keeps ``market_value_usd`` null, because a partial chain total would be a lie. Cash is
    the settlement asset (USD); its own value is always known, but its share is withheld
    while any open mark is unknown because the denominator (equity) is unknown.

    A chain has no single native quantity: tokens are not fungible across assets, so chain
    rows carry an asset count and a USD total while native quantity lives on the asset row.
    """
    now = now or utcnow()
    assets: list[dict[str, Any]] = []
    for view in views:
        market_value = view["market_value_usd"]
        value = None if market_value is None else Decimal(str(market_value))
        priced = view["mark_usd"] is not None
        assets.append(
            {
                "chain": view["chain"],
                "token_address": view["token_address"],
                "symbol": view["symbol"],
                "kind": "position",
                "quantity": view["quantity"],
                "mark_usd": view["mark_usd"],
                "market_value_usd": market_value,
                "share_pct": (None if not complete or value is None else _share_pct(value, equity)),
                # A source describes where a mark came from. An unmarked row keeps no
                # source: otherwise the UI could show an observation age as if the row
                # were priced (CONTRACT I3).
                "source": view["source"] if priced else None,
                "mark_status": (
                    "unknown"
                    if not priced
                    else _mark_status(view["mark_usd"], view["source"], max_age_seconds, now)
                ),
            }
        )

    chains: list[dict[str, Any]] = []
    for chain in sorted({str(view["chain"]) for view in views}):
        members = [
            asset for asset in assets if asset["kind"] == "position" and asset["chain"] == chain
        ]
        unpriced = sum(1 for asset in members if asset["market_value_usd"] is None)
        total = (
            None
            if unpriced
            else sum((Decimal(str(asset["market_value_usd"])) for asset in members), Decimal(0))
        )
        chains.append(
            {
                "chain": chain,
                "assets": len(members),
                "unpriced_assets": unpriced,
                "market_value_usd": None if total is None else str(total),
                "share_pct": (None if total is None or not complete else _share_pct(total, equity)),
                "mark_status": (
                    "unknown"
                    if unpriced
                    else _chain_mark_status([a for a in assets if a["chain"] == chain])
                ),
            }
        )

    cash_share = _share_pct(cash, equity) if complete else None
    chains.append(
        {
            "chain": SETTLEMENT_CHAIN,
            "assets": 1,
            "unpriced_assets": 0,
            "market_value_usd": str(cash),
            "share_pct": cash_share,
            "mark_status": "fresh",
        }
    )
    assets.append(
        {
            "chain": SETTLEMENT_CHAIN,
            "token_address": None,
            "symbol": SETTLEMENT_ASSET,
            "kind": "cash",
            "quantity": str(cash),
            "mark_usd": "1",
            "market_value_usd": str(cash),
            "share_pct": cash_share,
            "source": None,
            "mark_status": "fresh",
        }
    )
    return chains, assets


def _wallet_composition(
    views: list[dict[str, Any]],
    wallet_rows: list[WalletView],
    replay: Replay,
    *,
    complete: bool,
    equity: Decimal,
) -> list[dict[str, Any]]:
    """Per-wallet contribution to equity: settled cash plus the chain's marked positions.

    A wallet holds cash; the positions its chain holds are the other half of that chain's
    contribution. The settlement wallet owns the unspent genesis cash and no positions.
    The rows add up to equity, so the wallet view is a decomposition of the same total,
    not a second one. An unpriced position on a chain withholds that wallet's
    ``positions_usd`` and ``market_value_usd``, and never values it at zero.

    A position chain with no wallet row, and cash attributed to a wallet id that has no
    row, keep their value in an explicit ``unallocated`` row instead of disappearing from
    the total. The account is still one account; only its attribution is incomplete.
    """
    positions_by_chain: dict[str, list[dict[str, Any]]] = {}
    for view in views:
        positions_by_chain.setdefault(str(view["chain"]), []).append(view)

    rows: list[dict[str, Any]] = []
    # The unallocated bucket in ``wallet_rows`` is a cash read model, not a declared
    # wallet: only declared wallet rows take the per-wallet loop. The bucket's cash is
    # added below with the positions of chains that have no wallet, exactly once.
    declared_wallets = [wallet for wallet in wallet_rows if wallet.id is not None]
    allocated = {wallet.chain for wallet in declared_wallets}
    declared_ids = {wallet.id for wallet in declared_wallets}
    for wallet in declared_wallets:
        members = (
            positions_by_chain.get(wallet.chain, []) if wallet.chain != SETTLEMENT_CHAIN else []
        )
        unpriced = sum(1 for view in members if view["market_value_usd"] is None)
        positions_value = (
            None
            if unpriced
            else sum((Decimal(str(view["market_value_usd"])) for view in members), Decimal(0))
        )
        rows.append(
            _wallet_row(
                wallet_id=wallet.id,
                chain=wallet.chain,
                asset=wallet.asset,
                label=wallet.label,
                settled=wallet.balance_usd,
                positions_value=positions_value,
                unpriced=unpriced,
                complete=complete,
                equity=equity,
            )
        )

    unallocated = sorted(chain for chain in positions_by_chain if chain not in allocated)
    unattributed_cash = sum(
        (
            balance
            for wallet_id, balance in replay.wallet_cash.items()
            if wallet_id not in declared_ids
        ),
        Decimal(0),
    )
    if unallocated or unattributed_cash != 0:
        members = [view for chain in unallocated for view in positions_by_chain[chain]]
        unpriced = sum(1 for view in members if view["market_value_usd"] is None)
        positions_value = (
            None
            if unpriced
            else sum((Decimal(str(view["market_value_usd"])) for view in members), Decimal(0))
        )
        rows.append(
            _wallet_row(
                wallet_id=None,
                chain="unallocated",
                asset=SETTLEMENT_ASSET,
                label="Unallocated paper cash and positions",
                settled=unattributed_cash,
                positions_value=positions_value,
                unpriced=unpriced,
                complete=complete,
                equity=equity,
            )
        )
    return rows


def _wallet_row(
    *,
    wallet_id: str | None,
    chain: str,
    asset: str,
    label: str,
    settled: Decimal,
    positions_value: Decimal | None,
    unpriced: int,
    complete: bool,
    equity: Decimal,
) -> dict[str, Any]:
    market_value = None if positions_value is None else settled + positions_value
    return {
        "wallet_id": wallet_id,
        "chain": chain,
        "asset": asset,
        "label": label,
        "settled_usd": str(settled),
        "positions_usd": None if positions_value is None else str(positions_value),
        "market_value_usd": None if market_value is None else str(market_value),
        "share_pct": (
            None if market_value is None or not complete else _share_pct(market_value, equity)
        ),
        "mark_status": "unknown" if unpriced else "known",
    }


def account_snapshot(session: Session) -> dict[str, Any]:
    """Cash, realized, unrealized, equity and composition projected from the ledger.

    A read model never writes: genesis and the wallet declarations are bootstrapped at
    boot and on order placement, so this function can report a deleted wallet row as
    missing instead of healing the state it is meant to expose. Marks come from the
    latest market snapshot. A position without a mark reports ``mark_usd = null`` and
    makes the account incomplete: ``equity_usd`` and ``unrealized_usd`` are withheld
    (CONTRACT I3), while ``known_market_value_usd`` names the partial sum explicitly.
    ``by_asset``, ``by_chain`` and ``by_wallet`` are always server-computed; their shares
    are null while the total is unknown.

    ``wallets`` reports each wallet's identity and its settled cash and adds the
    ``unallocated`` bucket, and ``by_wallet`` decomposes the same equity into settled
    cash plus the marked positions of that wallet's chain. Both are projections of the
    ledger, never stored balances.
    """
    now = utcnow()
    max_age = get_settings().max_mark_age_seconds
    replay = _replay(_ledger_rows(session))
    views = _position_views(replay, _snapshot_index(session), max_age_seconds=max_age, now=now)
    known_market_value = Decimal(0)
    unrealized = Decimal(0)
    unpriced: list[dict[str, Any]] = []
    for view in views:
        market_value = view["market_value_usd"]
        if market_value is None:
            unpriced.append(
                {
                    "chain": view["chain"],
                    "token_address": view["token_address"],
                    "symbol": view["symbol"],
                    "quantity": view["quantity"],
                }
            )
            continue
        known_market_value += Decimal(str(market_value))
        unrealized += Decimal(str(view["unrealized_usd"]))
    complete = not unpriced
    equity = replay.cash + known_market_value
    max_age = get_settings().max_mark_age_seconds
    now = utcnow()
    by_chain, by_asset = _composition(
        views,
        replay.cash,
        complete=complete,
        equity=equity,
        max_age_seconds=max_age,
        now=now,
    )
    wallet_rows = wallet_views(session, replay)
    by_wallet = _wallet_composition(views, wallet_rows, replay, complete=complete, equity=equity)
    return {
        "cash_usd": str(replay.cash),
        "equity_usd": str(equity) if complete else None,
        "realized_usd": str(replay.realized),
        "unrealized_usd": str(unrealized) if complete else None,
        "complete": complete,
        "marks_unknown": not complete,
        "known_market_value_usd": str(known_market_value),
        "unpriced_positions": unpriced,
        "by_chain": by_chain,
        "by_asset": by_asset,
        "wallets": [view.to_dict() for view in wallet_rows],
        "by_wallet": by_wallet,
        "positions": views,
        "mode": "paper",
    }


def daily_realized_pnl(session: Session) -> Decimal:
    """Net realized PnL booked since UTC midnight, from the ledger only."""
    return _replay(_ledger_rows(session), day_start=_day_start()).daily_realized


def orders(session: Session, limit: int = 50) -> list[PaperOrderRow]:
    return list(
        session.scalars(
            select(PaperOrderRow).order_by(PaperOrderRow.created_at.desc()).limit(limit)
        )
    )


def fills(session: Session, limit: int = 50) -> list[PaperFillRow]:
    return list(
        session.scalars(select(PaperFillRow).order_by(PaperFillRow.created_at.desc()).limit(limit))
    )


# ---------------------------------------------------------------------------
# Order service
# ---------------------------------------------------------------------------


def trade_profile_from_settings(
    settings: Settings, requested: str | None = None
) -> TradeRiskProfile | None:
    name = requested if requested is not None else settings.trade_risk_profile
    if name is None:
        return None
    if name not in TRADE_RISK_PROFILES:
        raise ValueError("PROFILE_UNKNOWN")
    allowed = {settings.trade_risk_profile}
    if name not in allowed:
        raise ValueError("PROFILE_UNKNOWN")
    if name is None:
        return None
    return TRADE_RISK_PROFILES[name]


def freshness_from_settings(settings: Settings) -> FreshnessPolicy:
    """One freshness policy per process, built from the per-class settings budgets.

    The mark gate, the quote gate and the alert tracker all read the same instance, so a
    mark the order gate calls stale is exactly the mark the router refuses to price.
    """
    return FreshnessPolicy(
        max_age_by_class={
            "mark": float(settings.max_mark_age_seconds),
            "quote": float(settings.max_quote_age_seconds),
            "candle": float(settings.max_candle_age_seconds),
            "feed": float(settings.max_feed_age_seconds),
        }
    )


def limits_from_settings(settings: Settings) -> RiskLimits:
    profile = None
    if settings.trade_risk_profile is not None:
        profile = (
            TRADE_RISK_PROFILES.get(settings.trade_risk_profile)
            if settings.trade_risk_profile is not None
            else None
        )
    if profile is None:
        return RiskLimits(
            max_order_usd=settings.max_order_usd,
            max_position_usd=settings.max_position_usd,
            max_open_positions=settings.max_open_positions,
            max_daily_loss_usd=settings.max_daily_loss_usd,
            max_quote_age_seconds=float(settings.max_quote_age_seconds),
            max_mark_age_seconds=float(settings.max_mark_age_seconds),
            min_pair_age_minutes=settings.min_pair_age_minutes,
            min_liquidity_usd=settings.min_liquidity_usd,
            paper_mark_max_order_usd=settings.paper_mark_max_order_usd,
            max_price_impact_pct=settings.max_price_impact_pct,
        )
    return RiskLimits(
        min_order_usd=profile.min_order_usd,
        max_order_usd=min(settings.max_order_usd, profile.max_order_usd),
        max_order_pct_of_equity=profile.max_order_pct_of_equity,
        per_token_cap_usd=profile.per_token_cap_usd,
        max_slippage_bps=profile.max_slippage_bps,
        max_position_usd=settings.max_position_usd,
        max_open_positions=settings.max_open_positions,
        max_daily_loss_usd=settings.max_daily_loss_usd,
        max_quote_age_seconds=float(settings.max_quote_age_seconds),
        max_mark_age_seconds=float(settings.max_mark_age_seconds),
        min_pair_age_minutes=settings.min_pair_age_minutes,
        min_liquidity_usd=settings.min_liquidity_usd,
        paper_mark_max_order_usd=settings.paper_mark_max_order_usd,
        max_price_impact_pct=settings.max_price_impact_pct,
    )


def _fill_fee(notional: Decimal, quote: Quote, settings: Settings) -> Decimal:
    """The fill's fee: the larger of the paper fee and the quote's own cost.

    A ``paper_mark`` carries no venue cost, so the platform's flat paper fee is the whole
    fee. An executable quote already pays a pool/aggregator fee and gas, reported as
    ``quote.fee_usd``: charging the bps fee *on top* would bill the same liquidity twice,
    and charging only the bps fee would hide a 0.25% LI.FI fee or three dollars of gas.
    The maximum of the two is never less than either honest number and never their sum.

    The value is ``preview_cost``'s fill fee: the cost sheet previewed before the
    click and this journaled fee are one formula by construction, never two.
    """
    return _preview_cost(notional, quote, settings).fill_fee_usd


def _preview_cost(notional: Decimal, quote: Quote, settings: Settings) -> CostPreview:
    """The full cost sheet for one quote at one notional, via the shared model."""
    return preview_cost(
        kind=quote.kind,
        notional_usd=notional,
        venue_fee_usd=quote.fee_usd,
        price_impact_pct=quote.price_impact_pct,
        amount_out=quote.amount_out,
        fee_bps=settings.paper_fee_bps,
        source=quote.source,
    )


def _record(
    quote: Quote, notional: Decimal, settings: Settings, *, reduce_only: bool = False
) -> dict[str, Any]:
    """The persisted intent record: the quote plus its pre-trade cost preview.

    Every order that reached a quote records this before it fires (or is rejected),
    so the recorded preview belongs to the intent, never to a strategy: automated and
    manual orders share this path, and the preview is replayable from the order row.
    A reduce-only exit also records the flag, so the entry-gate exemption that was
    applied to this decision is visible on the order it produced.
    """
    record = quote.to_dict()
    record["cost_preview"] = _preview_cost(notional, quote, settings).to_dict()
    if reduce_only:
        record["reduce_only"] = True
    return record


class IdempotencyConflict(Exception):
    """Raised when an idempotency key is reused with a different request payload.

    The key names a single order: replaying it with the same payload returns the
    original order, but a changed payload must surface as ``409
    IDEMPOTENCY_CONFLICT`` instead of silently succeeding.
    """

    code = "IDEMPOTENCY_CONFLICT"

    def __init__(self, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__("idempotency key was already used for a different order payload")


# Every field that gives the key its meaning. Adding a payload field without adding it
# here would let one key name two different orders, so the schema-drift test in
# tests/paper/test_orders.py fails until the new field is covered. Rows written before
# the fingerprint column have no stored hash; their values are read from the persisted
# order, so a new field also needs a column and a backfill at that point.
_FINGERPRINT_FIELDS = (
    "chain",
    "token_address",
    "side",
    "amount_usd",
    "strategy_id",
    "quantity",
    "reduce_only",
    "risk_profile",
)
# Fields added after the fingerprint column existed have no stored order column. A row
# written before them is replayed with the value it could have had: no exact quantity and
# not a reduce-only exit. A request that supplies either value therefore conflicts with a
# legacy row instead of replaying a different intent.
_LEGACY_FINGERPRINT_DEFAULTS = {"quantity": None, "reduce_only": False, "risk_profile": None}


def _canonical_decimal(value: Decimal) -> str:
    """One spelling per value, so ``50``, ``50.0`` and ``50.00`` hash alike."""
    if not value.is_finite():
        raise ValueError("a fingerprint cannot be built from a non-finite decimal")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _fingerprint(values: dict[str, Any]) -> str:
    """SHA-256 of a payload's canonical JSON: sorted keys, no whitespace, exact money.

    ``None`` fields are omitted rather than encoded as ``null``, so an optional field
    that a caller does not supply hashes the same as one added later with a ``None``
    default. A supplied value still changes the hash.
    """
    canonical = {
        name: _canonical_decimal(value) if isinstance(value, Decimal) else value
        for name, value in values.items()
        if value is not None
    }
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _request_fingerprint(payload: OrderRequest) -> str:
    """Canonical hash of the request payload the idempotency key is bound to."""
    return _fingerprint({name: getattr(payload, name) for name in _FINGERPRINT_FIELDS})


def _stored_fingerprint(order: PaperOrderRow) -> str:
    """The fingerprint of the order as persisted, for rows written before 0011."""
    return _fingerprint(
        {
            name: getattr(order, name, _LEGACY_FINGERPRINT_DEFAULTS.get(name))
            for name in _FINGERPRINT_FIELDS
        }
    )


def _find_order(session: Session, idempotency_key: str) -> PaperOrderRow | None:
    return session.scalar(
        select(PaperOrderRow).where(PaperOrderRow.idempotency_key == idempotency_key)
    )


def _replay_existing(
    session: Session, payload: OrderRequest, existing: PaperOrderRow
) -> PaperOrderRow:
    """Return the original order, proving the key names this exact payload.

    A row written before the fingerprint column has no stored hash. Derive it from
    the persisted order so an identical retry still replays while a changed payload
    is refused, then persist the derived hash so the row is checked directly next
    time. A row whose stored values were rounded by the database will not match a
    finer request amount: that is a conflict, never a silent replay.
    """
    expected = existing.request_fingerprint
    actual = _request_fingerprint(payload)
    if expected is None:
        expected = _stored_fingerprint(existing)
        if expected != actual:
            raise IdempotencyConflict(payload.idempotency_key)
        if not session.is_active:
            # A failed flush earlier in the request leaves the session unusable; the
            # replay is still valid, so start a clean transaction for the backfill.
            session.rollback()
        existing.request_fingerprint = actual
        # The replay path has no other writes; commit the backfill so it survives a
        # rolled-back or closed request session.
        session.commit()
    elif expected != actual:
        raise IdempotencyConflict(payload.idempotency_key)
    _ORDER_CREATED.set(False)
    return existing


def _claim_order(
    session: Session, payload: OrderRequest, actor: OrderActor
) -> PaperOrderRow | None:
    """Insert the order row to claim the idempotency key. None means another won."""
    order = PaperOrderRow(
        id=new_id(),
        idempotency_key=payload.idempotency_key,
        request_fingerprint=_request_fingerprint(payload),
        chain=payload.chain,
        token_address=payload.token_address,
        side=payload.side,
        amount_usd=payload.amount_usd,
        status="pending",
        strategy_id=payload.strategy_id,
        actor_kind=actor.kind,
        actor_id=actor.id,
        created_at=utcnow(),
    )
    session.add(order)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return None
    return order


def _claim_or_existing(
    session: Session, payload: OrderRequest, actor: OrderActor
) -> tuple[PaperOrderRow, bool]:
    """Return (order, created). ``created`` is False when the key already existed."""
    order = _claim_order(session, payload, actor)
    if order is not None:
        _ORDER_CREATED.set(True)
        return order, True
    existing = _find_order(session, payload.idempotency_key)
    if existing is None:  # pragma: no cover - the unique index guarantees a row exists
        raise RuntimeError("idempotency claim lost without an existing order")
    return _replay_existing(session, payload, existing), False


def _structured_reasons(value: Any) -> list[dict[str, Any]]:
    """Normalize a reason code, string or RiskReason into the persisted shape."""
    if isinstance(value, str):
        items = [part for part in value.split(",") if part]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = [value]
    out: list[dict[str, Any]] = []
    for item in items:
        if hasattr(item, "to_dict"):
            out.append(item.to_dict())
        else:
            code = str(item)
            out.append(
                {
                    "code": code,
                    "message": RISK_REASON_MESSAGES.get(code, code),
                    "detail": None,
                }
            )
    return out


def _reject_new(
    session: Session,
    payload: OrderRequest,
    reason: str,
    snapshot: TokenSnapshot | None = None,
    quote_json: dict[str, Any] | None = None,
    reasons: Any = None,
    *,
    actor: OrderActor = UNKNOWN_ACTOR,
) -> PaperOrderRow:
    """Persist a rejection before a quote exists (insufficient position, no exit route)."""
    order, created = _claim_or_existing(session, payload, actor)
    if not created:
        return order
    if snapshot is not None:
        record_snapshot(session, snapshot)
    order.status = "rejected"
    order.reason = reason
    order.reasons = _structured_reasons(reasons if reasons is not None else reason)
    order.quote_json = quote_json
    session.commit()
    return order


def _reject_after_quote(
    session: Session,
    payload: OrderRequest,
    reason: str,
    *,
    snapshot: TokenSnapshot,
    quote: Quote,
    settings: Settings,
    reduce_only: bool = False,
    reasons: Any = None,
    actor: OrderActor = UNKNOWN_ACTOR,
) -> PaperOrderRow:
    """Persist a rejection that has a quote, so the refused size stays replayable.

    The recorded preview is this order's (the intent's), never a strategy's: the order
    row carries the quote and the cost sheet computed before the refusal.
    """
    order, created = _claim_or_existing(session, payload, actor)
    if not created:
        return order
    record_snapshot(session, snapshot)
    order.status = "rejected"
    order.reason = reason
    order.reasons = _structured_reasons(reasons if reasons is not None else reason)
    order.quote_kind = quote.kind
    notional = quote.amount_in if payload.side == "buy" else quote.amount_out
    order.quote_json = _record(quote, notional, settings, reduce_only=reduce_only)
    session.commit()
    return order


def _mark_rejected(
    session: Session, order: PaperOrderRow, reason: str, reasons: Any = None
) -> PaperOrderRow:
    order.status = "rejected"
    order.reason = reason
    order.reasons = _structured_reasons(reasons if reasons is not None else reason)
    session.commit()
    return order


def _attach_quote_price(snapshot: TokenSnapshot, quote: Quote) -> TokenSnapshot:
    """Use the executable quote price when the market feed has no price for the token."""
    if snapshot.price_usd is not None or quote.price <= 0:
        return snapshot
    return replace(snapshot, price_usd=quote.price, source=quote.source)


def _sell_quantity(
    payload: OrderRequest, snapshot: TokenSnapshot, held_qty: Decimal, *, mark_usable: bool
) -> Decimal | None:
    """The token quantity to quote for a sell, never more than is held.

    Quantity comes first: a caller that knows the position (a protection arm, a
    position screen) sends the token amount, so the exit is exact even when no mark is
    available. Without one, a normal sell converts its USD notional at the market mark
    and fails closed (``None``) without a usable mark. A reduce-only exit with no
    quantity asks for the whole position: the exit sizes from the *held quantity*, never
    from the mark, and its executable route is the price. A stale mark therefore cannot
    trap a position by deciding how much of it to leave behind.
    """
    if payload.quantity is not None:
        return min(payload.quantity, held_qty)
    if not mark_usable:
        return held_qty if payload.reduce_only else None
    mark = snapshot.price_usd
    assert mark is not None
    return min(payload.amount_usd / mark, held_qty)


def _exit_evidence(
    *,
    held_qty: Decimal,
    quantity: Decimal,
    reduce_only: bool,
    error: AdapterError,
) -> dict[str, Any]:
    """Why an exit reached no quote: the router's refusal, kept on the order row."""
    return {
        "side": "sell",
        "reduce_only": reduce_only,
        "held_quantity": str(held_qty),
        "requested_quantity": str(quantity),
        "error": {"code": error.code, "message": error.message, "detail": error.detail},
    }


async def place_order(
    session: Session,
    payload: OrderRequest,
    settings: Settings,
    dexscreener: DexScreenerClient,
    quotes: QuoteRouter,
    *,
    actor: OrderActor | None = None,
) -> PaperOrderRow:
    """Place a paper order: quote, risk, fill and journal in one transaction.

    Cash, the open-position count and every position are one paper account, so the fill
    window is serialized per *account*, not per token. The quote happens outside a
    transaction (it is network I/O); the replay, order claim and journal then run under one
    in-process lock, and PostgreSQL additionally takes a transaction-scoped advisory lock
    so the guard holds across API processes. A sell whose quoted quantity is no longer
    held is rejected ``POSITION_CHANGED`` rather than resized against a stale quote: the
    trader retries with a fresh, executable size.

    ``actor`` is the authenticated principal the API edge resolved. It is never a request
    field: the service decides the recorded actor from ``strategy_id`` and this parameter
    alone, so a caller that omits it writes ``unknown`` instead of a guessed name. The
    actor is stamped on the order row and on every leg of its journal group.
    """
    placed_by = resolve_actor(payload, actor)
    _ORDER_CREATED.set(True)
    ensure_genesis(session, settings.paper_starting_cash_usd)
    existing = _find_order(session, payload.idempotency_key)
    if existing is not None:
        return _replay_existing(session, payload, existing)

    # Integrity before any network call. The API schema already refuses these; an
    # internal caller that skips the edge still gets a typed rejection, not a quote.
    if payload.side not in ("buy", "sell"):
        return _reject_new(session, payload, "INVALID_SIDE", actor=placed_by)
    if payload.amount_usd <= 0:
        return _reject_new(session, payload, "INVALID_AMOUNT", actor=placed_by)

    # The operator switch is the broadest gate and it is not scoped by attribution: a REST
    # order, an MCP tool and the web ticket carry no strategy id, so an armed switch must
    # be checked here for every caller. Only a buy can add risk; a sell is bounded by the
    # held quantity and can never open a short, so exits -- including reduce-only
    # protection arms -- still settle while the switch is armed. The refusal happens
    # before the market read and the quote, so it touches no venue and journals nothing.
    # An unreadable switch fails closed with a typed refusal: it never assumes "released"
    # and never raises raw out of the order service.
    if payload.side == "buy":
        try:
            armed = read_kill_switch(session, settings).active
        except Exception:  # noqa: BLE001 - an unreadable switch must refuse the entry
            return _reject_new(session, payload, "KILL_SWITCH_UNAVAILABLE", actor=placed_by)
        if armed:
            return _reject_new(session, payload, "KILL_SWITCH_ARMED", actor=placed_by)

    # Settlement routing is an entry gate. A buy must settle to a wallet that exists for
    # its chain and declares the ledger asset, so a refusal names the chain and the asset
    # asked for instead of guessing an account (CONTRACT I3). A sell is bounded by the
    # held quantity and can never open a short, so the gate must not refuse one: a
    # missing or wrong-asset wallet would otherwise trap an exit, and a reduce-only exit
    # is never blocked by an entry gate. An exit's proceeds settle to its chain's wallet
    # when that wallet is valid, otherwise to the settlement wallet; cash attributed to a
    # wallet row that is gone stays visible in the read model's unallocated bucket.
    wallet, wallet_reason = wallet_for_chain(session, payload.chain)
    if payload.side == "buy":
        if wallet is None or wallet_reason is not None:
            code = wallet_reason or "WALLET_UNKNOWN"
            detail: dict[str, Any] = {"chain": payload.chain}
            if wallet is not None:
                detail["asset"] = wallet.asset
            return _reject_new(
                session,
                payload,
                code,
                reasons=(RiskReason(code, detail=detail),),
                actor=placed_by,
            )
        # The id survives the rollback before the quote network call; the ORM instance need not.
        settlement_wallet_id = wallet.id
    else:
        settlement_wallet_id = (
            wallet.id if wallet is not None and wallet_reason is None else SETTLEMENT_WALLET_ID
        )

    # Profile selection is server-side. An unknown or disallowed profile is a typed
    # rejection and never falls back to the active profile.
    requested_profile = payload.risk_profile
    if requested_profile is not None:
        try:
            trade_profile_from_settings(settings, requested_profile)
        except ValueError:
            return _reject_new(session, payload, "PROFILE_UNKNOWN", actor=placed_by)

    if payload.side == "buy" and settings.trade_risk_profile is not None:
        profile = (
            TRADE_RISK_PROFILES.get(settings.trade_risk_profile)
            if settings.trade_risk_profile is not None
            else None
        )
        if profile is None:
            return _reject_new(session, payload, "PROFILE_UNKNOWN", actor=placed_by)
        # Minimum size and (absolute) maximum size apply before any quote.
        if payload.amount_usd < profile.min_order_usd:
            return _reject_new(session, payload, "SIZE_BELOW_MIN", actor=placed_by)
        if payload.amount_usd > profile.max_order_usd:
            return _reject_new(session, payload, "SIZE_ABOVE_MAX", actor=placed_by)
        # Percent of equity, fail-closed on unknown marks.
        replay = _replay(_ledger_rows(session))
        snapshots = _snapshot_index(session)
        equity: Decimal = replay.cash
        equity_known = True
        for account_pos, (qty, _cost) in replay.positions.items():
            if qty <= 0:
                continue
            chain, token = _split_position_account(account_pos)
            snap = snapshots.get((chain, token))
            mark = snap.price_usd if snap is not None else None
            if mark is None:
                equity_known = False
                break
            equity = equity + qty * mark
        if not equity_known:
            return _reject_new(session, payload, "EQUITY_UNKNOWN", actor=placed_by)
        cap = equity * profile.max_order_pct_of_equity
        if payload.amount_usd > cap:
            return _reject_new(session, payload, "SIZE_ABOVE_MAX", actor=placed_by)
        # Per-token cap including the proposed order.
        existing_value = Decimal(0)
        held_qty, held_cost = replay.positions.get(
            position_account(payload.chain, payload.token_address), (Decimal(0), Decimal(0))
        )
        snap = snapshots.get((payload.chain, payload.token_address))
        mark = snap.price_usd if snap is not None else None
        if mark is not None:
            existing_value = held_qty * mark
        if existing_value + payload.amount_usd > profile.per_token_cap_usd:
            return _reject_new(session, payload, "POSITION_LIMIT", actor=placed_by)

    account = position_account(payload.chain, payload.token_address)
    held_qty = Decimal(0)
    reduce_only = payload.side == "sell" and payload.reduce_only
    if payload.side == "sell":
        # Size the quote from a read-only snapshot, then close the transaction before the
        # network call. The authoritative read happens again under the position lock.
        held_qty, _ = _replay(_ledger_rows(session)).positions.get(
            account, (Decimal(0), Decimal(0))
        )

    # Do not hold a transaction open across the quote network call.
    session.rollback()
    snapshot = await dexscreener.get_token(
        payload.chain, payload.token_address, max_age_seconds=settings.max_mark_age_seconds
    )

    if payload.side == "sell":
        if held_qty <= 0:
            return _reject_new(
                session, payload, "INSUFFICIENT_POSITION", snapshot=snapshot, actor=placed_by
            )
        quantity = _sell_quantity(
            payload,
            snapshot,
            held_qty,
            mark_usable=_mark_can_price(snapshot, settings.max_mark_age_seconds),
        )
        if quantity is None:
            return _reject_new(
                session, payload, "PRICE_UNKNOWN", snapshot=snapshot, actor=placed_by
            )
        if quantity <= 0:
            return _reject_new(
                session, payload, "INVALID_AMOUNT", snapshot=snapshot, actor=placed_by
            )
        # Sell providers size by quantity. The USD figure is a metadata hint only and is
        # absent when the market mark is; it never decides the exit's size.
        quote_amount_usd = (
            quantity * snapshot.price_usd
            if snapshot.price_usd is not None and snapshot.price_usd > 0
            else Decimal(0)
        )
        try:
            quote = await quotes.quote(
                chain=payload.chain,
                token_address=payload.token_address,
                side="sell",
                amount_usd=quote_amount_usd,
                quantity=quantity,
            )
        except NoRoute as exc:
            # No fill without a route. The refusal is typed and persisted with the
            # router's own evidence instead of disappearing into an HTTP error.
            return _reject_new(
                session,
                payload,
                "EXIT_NO_ROUTE",
                snapshot=snapshot,
                quote_json=_exit_evidence(
                    held_qty=held_qty,
                    quantity=quantity,
                    reduce_only=reduce_only,
                    error=exc,
                ),
                actor=placed_by,
            )
    else:
        try:
            quote = await quotes.quote(
                chain=payload.chain,
                token_address=payload.token_address,
                side="buy",
                amount_usd=payload.amount_usd,
                quantity=None,
            )
        except NoRoute as exc:
            # A stale-mark refusal is a data-quality fact worth recording: the order row
            # persists the router's typed reason (``SNAPSHOT_STALE``), exactly as a
            # kill-switch refusal does. A transient route/timeout failure is not claimed
            # here, so the same idempotency key can retry instead of replaying a refusal.
            if exc.code != "SNAPSHOT_STALE":
                raise
            return _reject_new(
                session,
                payload,
                exc.code,
                snapshot=snapshot,
                quote_json={
                    "side": "buy",
                    "error": {
                        "code": exc.code,
                        "message": exc.message,
                        "detail": exc.detail,
                    },
                },
            )

    snapshot = _attach_quote_price(snapshot, quote)

    with _serialized_paper_fill(session):
        # The gate above ran before the market read and the quote. Re-read the persisted
        # switch inside the fill window so an operator who arms mid-flight is obeyed by
        # the order this intent is about to book, not only by the next one. Only a buy
        # adds risk: an exit must still settle, as it does at the first gate. An
        # unreadable switch refuses the buy with a typed code, never a raw exception.
        if payload.side == "buy":
            try:
                armed = read_kill_switch(session, settings).active
            except Exception:  # noqa: BLE001 - fail closed; place_order returns, never raises
                return _reject_after_quote(
                    session,
                    payload,
                    "KILL_SWITCH_UNAVAILABLE",
                    snapshot=snapshot,
                    quote=quote,
                    settings=settings,
                    actor=placed_by,
                )
            if armed:
                return _reject_after_quote(
                    session,
                    payload,
                    "KILL_SWITCH_ARMED",
                    snapshot=snapshot,
                    quote=quote,
                    settings=settings,
                    actor=placed_by,
                )

        # Replay under the lock: this is the only position view the fill may use.
        replay = _replay(_ledger_rows(session), day_start=_day_start())
        held_qty, held_cost = replay.positions.get(account, (Decimal(0), Decimal(0)))
        # An exit is either explicitly flagged or sized to the whole position. The bound
        # is computed from the authoritative held quantity, never from the pre-quote
        # read, so a position that grew in between makes this a partial sell again.
        exit_only = payload.side == "sell" and (
            payload.reduce_only or (held_qty > 0 and quote.amount_in >= held_qty - MONEY_QUANTUM)
        )

        if payload.side == "sell":
            if held_qty <= 0:
                return _reject_after_quote(
                    session,
                    payload,
                    "INSUFFICIENT_POSITION",
                    snapshot=snapshot,
                    quote=quote,
                    settings=settings,
                    reduce_only=exit_only,
                    actor=placed_by,
                )
            # Never fill a quantity that another writer already spent. A smaller quote is
            # fine (the position grew); a larger quote is stale and must be re-priced.
            if quote.amount_in > held_qty + MONEY_QUANTUM:
                return _reject_after_quote(
                    session,
                    payload,
                    "POSITION_CHANGED",
                    snapshot=snapshot,
                    quote=quote,
                    settings=settings,
                    reduce_only=exit_only,
                    actor=placed_by,
                )
            fill_quantity = min(quote.amount_in, held_qty)
            notional = (
                quote.amount_out
                if fill_quantity == quote.amount_in
                else fill_quantity * quote.price
            )
        else:
            fill_quantity = quote.amount_out
            notional = quote.amount_in

        order, created = _claim_or_existing(session, payload, placed_by)
        if not created:
            return order
        record_snapshot(session, snapshot)

        if fill_quantity <= 0 or notional <= 0:
            order.quote_kind = quote.kind
            # ``_record`` needs a positive notional for the cost sheet; a malformed
            # zero-size quote has no cost, only the route evidence.
            order.quote_json = {
                "quote": quote.to_dict(),
                "reduce_only": exit_only,
                "held_quantity": str(held_qty),
            }
            return _mark_rejected(session, order, "EXIT_NO_ROUTE" if exit_only else "NO_ROUTE")

        position_mark = snapshot.price_usd if snapshot.price_usd is not None else quote.price
        request = OrderRequest(
            chain=payload.chain,
            token_address=payload.token_address,
            side=payload.side,
            amount_usd=notional,
            idempotency_key=payload.idempotency_key,
            strategy_id=payload.strategy_id,
            reduce_only=exit_only,
        )
        _equity = replay.cash
        _equity_known = True
        for _acct, (_qty, _cost) in replay.positions.items():
            if _qty <= 0:
                continue
            _ch, _tok = _split_position_account(_acct)
            _snap = _snapshot_index(session).get((_ch, _tok))
            _mark = _snap.price_usd if _snap is not None else None
            if _mark is None:
                _equity_known = False
                break
            _equity += _qty * _mark
        equity_for_risk = _equity if _equity_known else None

        allowed, reasons = evaluate_order(
            request=request,
            snapshot=snapshot,
            quote=quote,
            current_position_value_usd=held_qty * position_mark,
            open_positions=_open_positions(replay),
            daily_realized_pnl_usd=replay.daily_realized,
            limits=limits_from_settings(settings),
            equity_usd=equity_for_risk,
        )
        fee = _fill_fee(notional, quote, settings)
        funded_shortfall = Decimal(0)
        if allowed and payload.side == "buy" and replay.cash < notional + fee:
            if settings.paper_no_risk:
                funded_shortfall = (notional + fee) - replay.cash
            else:
                allowed = False
                reasons = (RiskReason("INSUFFICIENT_CASH"),)

        if not allowed:
            order.quote_kind = quote.kind
            order.quote_json = _record(quote, notional, settings, reduce_only=exit_only)
            return _mark_rejected(
                session, order, ",".join(str(r) for r in reasons), reasons=reasons
            )

        stamp = utcnow()
        if funded_shortfall > 0:
            funding_group = f"funding:{order.id}"
            funding_entries = (
                LedgerEntry(cash_account(), "USD", funded_shortfall, funded_shortfall, "funding"),
                LedgerEntry("funding:USD", "USD", -funded_shortfall, -funded_shortfall, "funding"),
            )
            from ezchain_core.ledger import assert_balanced

            assert_balanced(funding_entries)
            for entry in funding_entries:
                row = entry.to_row(funding_group, created_at=stamp)
                # The no-risk top-up is settlement capital, not a venue settlement: its
                # cash leg names the settlement wallet so the journal says where the money
                # went. The ``funding:USD`` contra leg is not a cash account and carries
                # no wallet id.
                if row["account"] == cash_account():
                    row["wallet_id"] = SETTLEMENT_WALLET_ID
                row["actor_kind"] = placed_by.kind
                row["actor_id"] = placed_by.id
                session.add(LedgerEntryRow(**row))
        if payload.side == "buy":
            entries = buy_entries(
                chain=payload.chain,
                token_address=payload.token_address,
                quantity=fill_quantity,
                price_usd=quote.price,
                fee_bps=settings.paper_fee_bps,
                notional_usd=notional,
                fee_usd=fee,
            )
        else:
            avg_cost = held_cost / held_qty if held_qty > 0 else Decimal(0)
            if fill_quantity == held_qty:
                basis = held_cost
            else:
                basis = min((fill_quantity * avg_cost).quantize(MONEY_QUANTUM), held_cost)
            entries = sell_entries(
                chain=payload.chain,
                token_address=payload.token_address,
                quantity=fill_quantity,
                price_usd=quote.price,
                avg_cost_usd=avg_cost,
                fee_bps=settings.paper_fee_bps,
                proceeds_usd=notional,
                cost_basis_usd=basis,
                fee_usd=fee,
            )

        session.add(
            PaperFillRow(
                id=new_id(),
                order_id=order.id,
                chain=payload.chain,
                token_address=payload.token_address,
                side=payload.side,
                quantity=fill_quantity,
                price_usd=quote.price,
                notional_usd=notional,
                fee_usd=fee,
                quote_kind=quote.kind,
                created_at=stamp,
            )
        )
        for entry in entries:
            row = entry.to_row(order.id, created_at=stamp)
            if row["account"] == cash_account():
                row["wallet_id"] = settlement_wallet_id
            # The journal keeps its own attribution: a reader of ``ledger_entries`` alone
            # learns who placed the order, without joining back to ``paper_orders``.
            row["actor_kind"] = placed_by.kind
            row["actor_id"] = placed_by.id
            session.add(LedgerEntryRow(**row))

        order.status = "filled"
        order.filled_at = stamp
        order.quote_kind = quote.kind
        order.quote_json = _record(quote, notional, settings, reduce_only=exit_only)
        if funded_shortfall > 0:
            order.reason = "SHORTFALL_FUNDED"
            order.reasons = _structured_reasons(RiskReason("SHORTFALL_FUNDED"))
        session.commit()
        return order


# One paper account means one fill window. Serializing every fill is deliberately
# boring: it makes cash, open-position count and every position agree at commit time.
# The quote network call stays outside this lock.
_PAPER_ORDER_LOCK = threading.Lock()


@contextmanager
def _serialized_paper_fill(session: Session) -> Iterator[None]:
    """Serialize the read-claim-fill window for the single paper account.

    PostgreSQL adds an advisory transaction lock so two API processes cannot both spend
    the same cash or position. SQLite tests and the single-process API use the in-process
    lock. READ COMMITTED is required: each statement must see the winner's committed rows
    after the advisory lock is granted.
    """
    with _PAPER_ORDER_LOCK:
        bind = session.get_bind()
        if bind.dialect.name == "postgresql":
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": "paper:account"}
            )
        yield
