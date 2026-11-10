"""Paper order service and ledger-derived read models.

The ledger is the only accountant (CONTRACT I1). An order is claimed by its unique
idempotency key, risk is evaluated before any fill, and the order, fill and ledger journal
commit in one transaction. Cash, positions and PnL are projected by replaying ledger
entries, never read from a mutable scalar.

Every sell is bounded by the held quantity and can never open a short. A reduce-only exit
— a sell explicitly flagged ``reduce_only``, or one whose requested size clamps to the
whole position — bypasses entry-only gates: its executable sell quote is the exit's
price, so a missing or stale market mark may not trap the position. Exit integrity still
fails closed: a held position, a fresh quote, an executable route and a quantity no
larger than the position are all required, and a sell with no route is persisted as
``EXIT_NO_ROUTE`` with its quote evidence.
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
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ezchain_adapters import AdapterError, DexScreenerClient, NoRoute, QuoteRouter
from ezchain_core import (
    TRADE_RISK_PROFILES,
    CostPreview,
    LedgerEntry,
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

from ..config import Settings
from ..models import LedgerEntryRow, MarketSnapshotRow, PaperFillRow, PaperOrderRow, new_id
from . import market_snapshots
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


POSITION_PREFIX = "position:"
MONEY_QUANTUM = Decimal("0.00000001")
SHARE_QUANTUM = Decimal("0.01")
SETTLEMENT_CHAIN = "settlement"
SETTLEMENT_ASSET = "USD"


# ---------------------------------------------------------------------------
# Genesis and replay
# ---------------------------------------------------------------------------


def ensure_genesis(session: Session, starting_cash_usd: Decimal) -> None:
    """Create the genesis journal exactly once, keyed by its group id."""
    existing = session.scalar(
        select(LedgerEntryRow.id).where(LedgerEntryRow.group_id == GENESIS_GROUP).limit(1)
    )
    if existing is not None:
        return
    for entry in genesis_entries(starting_cash_usd):
        session.add(LedgerEntryRow(**entry.to_row(GENESIS_GROUP)))
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
    """
    cash = Decimal(0)
    groups: dict[str, list[LedgerEntryRow]] = {}
    position_entries: dict[str, list[LedgerEntry]] = {}

    for row in rows:
        if row.account == cash_account():
            cash += row.usd_value
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


def record_snapshot(session: Session, snapshot: TokenSnapshot) -> MarketSnapshotRow:
    """Upsert the latest observation for (chain, token); older events never overwrite."""
    return market_snapshots.record_snapshot(session, snapshot)


def _snapshot_index(session: Session) -> dict[tuple[str, str], MarketSnapshotRow]:
    rows = session.scalars(select(MarketSnapshotRow).order_by(MarketSnapshotRow.observed_at)).all()
    return {(row.chain, row.token_address): row for row in rows}


def _source_ref(row: MarketSnapshotRow) -> SourceRef:
    observed = _aware(row.observed_at) or utcnow()
    return SourceRef(name=row.source, url=None, observed_at=observed)


# ---------------------------------------------------------------------------
# Read models
# ---------------------------------------------------------------------------


def _position_views(
    replay: Replay, snapshots: dict[tuple[str, str], MarketSnapshotRow]
) -> list[dict[str, Any]]:
    views: list[dict[str, Any]] = []
    for account, (quantity, cost) in replay.positions.items():
        if quantity <= 0:
            continue
        chain, token = _split_position_account(account)
        snapshot = snapshots.get((chain, token))
        mark = snapshot.price_usd if snapshot is not None else None
        market_value = None if mark is None else quantity * mark
        unrealized = None if market_value is None else market_value - cost
        view = PositionView(
            chain=chain,
            token_address=token,
            symbol=snapshot.symbol if snapshot is not None and snapshot.symbol else token,
            quantity=quantity,
            avg_cost_usd=cost / quantity,
            mark_usd=mark,
            market_value_usd=market_value,
            unrealized_usd=unrealized,
            realized_usd=replay.realized_by_account.get(account, Decimal(0)),
            # A source describes where a mark came from. A snapshot row can exist with no
            # price (a failed observation); labelling that row with a source would present
            # it as priced and leak an observation age to dashboard/positions/reports.
            source=None if snapshot is None or mark is None else _source_ref(snapshot),
            mode="paper",
        )
        views.append(view.to_dict())
    views.sort(key=lambda item: (item["chain"], item["token_address"]))
    return views


def positions(session: Session) -> list[dict[str, Any]]:
    return _position_views(_replay(_ledger_rows(session)), _snapshot_index(session))


def _share_pct(value: Decimal, equity: Decimal) -> str | None:
    """A row's share of equity, computed only when the equity total is usable."""
    if equity <= 0:
        return None
    return str((value * 100 / equity).quantize(SHARE_QUANTUM))


def _composition(
    views: list[dict[str, Any]], cash: Decimal, *, complete: bool, equity: Decimal
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
                "mark_status": "unknown" if not priced else "known",
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
                "mark_status": "unknown" if unpriced else "known",
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
            "mark_status": "known",
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
            "mark_status": "known",
        }
    )
    return chains, assets


def account_snapshot(session: Session, starting_cash_usd: Decimal | None = None) -> dict[str, Any]:
    """Cash, realized, unrealized, equity and composition projected from the ledger.

    ``starting_cash_usd`` is optional so bootstrapping callers can create genesis before
    reading. Marks come from the latest market snapshot. A position without a mark reports
    ``mark_usd = null`` and makes the account incomplete: ``equity_usd`` and
    ``unrealized_usd`` are withheld (CONTRACT I3), while ``known_market_value_usd`` names
    the partial sum explicitly. ``by_asset`` and ``by_chain`` are always server-computed;
    their shares are null while the total is unknown.
    """
    if starting_cash_usd is not None:
        ensure_genesis(session, starting_cash_usd)
    replay = _replay(_ledger_rows(session))
    views = _position_views(replay, _snapshot_index(session))
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
    by_chain, by_asset = _composition(views, replay.cash, complete=complete, equity=equity)
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


def _claim_order(session: Session, payload: OrderRequest) -> PaperOrderRow | None:
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
        created_at=utcnow(),
    )
    session.add(order)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return None
    return order


def _claim_or_existing(session: Session, payload: OrderRequest) -> tuple[PaperOrderRow, bool]:
    """Return (order, created). ``created`` is False when the key already existed."""
    order = _claim_order(session, payload)
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
) -> PaperOrderRow:
    """Persist a rejection before a quote exists (insufficient position, no exit route)."""
    order, created = _claim_or_existing(session, payload)
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
) -> PaperOrderRow:
    """Persist a rejection that has a quote, so the refused size stays replayable.

    The recorded preview is this order's (the intent's), never a strategy's: the order
    row carries the quote and the cost sheet computed before the refusal.
    """
    order, created = _claim_or_existing(session, payload)
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
    payload: OrderRequest, snapshot: TokenSnapshot, held_qty: Decimal
) -> Decimal | None:
    """The token quantity to quote for a sell, never more than is held.

    Quantity comes first: a caller that knows the position (a protection arm, a
    position screen) sends the token amount, so the exit is exact even when no mark is
    available. Without one, a normal sell converts its USD notional at the market mark
    and fails closed (``None``) without it. A reduce-only exit with no quantity and no
    usable mark asks for the whole position: the router's executable quote is the
    exit's price, and a mark the platform cannot verify must not decide how much of
    the position to leave behind.
    """
    if payload.quantity is not None:
        return min(payload.quantity, held_qty)
    mark = snapshot.price_usd
    usable_mark = mark is not None and mark > 0 and not (payload.reduce_only and snapshot.stale)
    if not usable_mark:
        return held_qty if payload.reduce_only else None
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
) -> PaperOrderRow:
    """Place a paper order: quote, risk, fill and journal in one transaction.

    Cash, the open-position count and every position are one paper account, so the fill
    window is serialized per *account*, not per token. The quote happens outside a
    transaction (it is network I/O); the replay, order claim and journal then run under one
    in-process lock, and PostgreSQL additionally takes a transaction-scoped advisory lock
    so the guard holds across API processes. A sell whose quoted quantity is no longer
    held is rejected ``POSITION_CHANGED`` rather than resized against a stale quote: the
    trader retries with a fresh, executable size.
    """
    _ORDER_CREATED.set(True)
    ensure_genesis(session, settings.paper_starting_cash_usd)
    existing = _find_order(session, payload.idempotency_key)
    if existing is not None:
        return _replay_existing(session, payload, existing)

    # Integrity before any network call. The API schema already refuses these; an
    # internal caller that skips the edge still gets a typed rejection, not a quote.
    if payload.side not in ("buy", "sell"):
        return _reject_new(session, payload, "INVALID_SIDE")
    if payload.amount_usd <= 0:
        return _reject_new(session, payload, "INVALID_AMOUNT")

    # Defence in depth for callers that bypass KillSwitchExecutor: an order that claims
    # strategy attribution is refused before any quote while the operator switch is armed.
    if payload.strategy_id is not None and read_kill_switch(session, settings).active:
        return _reject_new(session, payload, "KILL_SWITCH_ACTIVE")

    # Profile selection is server-side. An unknown or disallowed profile is a typed
    # rejection and never falls back to the active profile.
    requested_profile = payload.risk_profile
    if requested_profile is not None:
        try:
            trade_profile_from_settings(settings, requested_profile)
        except ValueError:
            return _reject_new(session, payload, "PROFILE_UNKNOWN")

    if payload.side == "buy" and settings.trade_risk_profile is not None:
        profile = (
            TRADE_RISK_PROFILES.get(settings.trade_risk_profile)
            if settings.trade_risk_profile is not None
            else None
        )
        if profile is None:
            return _reject_new(session, payload, "PROFILE_UNKNOWN")
        # Minimum size and (absolute) maximum size apply before any quote.
        if payload.amount_usd < profile.min_order_usd:
            return _reject_new(session, payload, "SIZE_BELOW_MIN")
        if payload.amount_usd > profile.max_order_usd:
            return _reject_new(session, payload, "SIZE_ABOVE_MAX")
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
            return _reject_new(session, payload, "EQUITY_UNKNOWN")
        cap = equity * profile.max_order_pct_of_equity
        if payload.amount_usd > cap:
            return _reject_new(session, payload, "SIZE_ABOVE_MAX")
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
            return _reject_new(session, payload, "POSITION_LIMIT")

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
    snapshot = await dexscreener.get_token(payload.chain, payload.token_address)

    if payload.side == "sell":
        if held_qty <= 0:
            return _reject_new(session, payload, "INSUFFICIENT_POSITION", snapshot=snapshot)
        quantity = _sell_quantity(payload, snapshot, held_qty)
        if quantity is None:
            return _reject_new(session, payload, "PRICE_UNKNOWN", snapshot=snapshot)
        if quantity <= 0:
            return _reject_new(session, payload, "INVALID_AMOUNT", snapshot=snapshot)
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
            )
    else:
        quote = await quotes.quote(
            chain=payload.chain,
            token_address=payload.token_address,
            side="buy",
            amount_usd=payload.amount_usd,
            quantity=None,
        )

    snapshot = _attach_quote_price(snapshot, quote)

    with _serialized_paper_fill(session):
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

        order, created = _claim_or_existing(session, payload)
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
                session.add(LedgerEntryRow(**entry.to_row(funding_group, created_at=stamp)))
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
            session.add(LedgerEntryRow(**entry.to_row(order.id, created_at=stamp)))

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
