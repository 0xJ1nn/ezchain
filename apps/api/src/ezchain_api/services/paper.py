"""Paper order service and ledger-derived read models.

The ledger is the only accountant (CONTRACT I1). An order is claimed by its unique
idempotency key, risk is evaluated before any fill, and the order, fill and ledger journal
commit in one transaction. Cash, positions and PnL are projected by replaying ledger
entries, never read from a mutable scalar.
"""

from __future__ import annotations

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

from ezchain_adapters import PAPER_MARK, DexScreenerClient, QuoteRouter
from ezchain_core import (
    LedgerEntry,
    OrderRequest,
    PositionView,
    Quote,
    RiskLimits,
    Side,
    SourceRef,
    TokenSnapshot,
    bps_fee,
    evaluate_order,
    genesis_entries,
    replay_position,
    utcnow,
)
from ezchain_core.ledger import (
    buy_entries,
    cash_account,
    position_account,
    sell_entries,
)

from ..config import Settings
from ..models import LedgerEntryRow, MarketSnapshotRow, PaperFillRow, PaperOrderRow, new_id
from . import trade_policy
from .system_flags import read_kill_switch

GENESIS_GROUP = "genesis"
POSITION_PREFIX = "position:"
MONEY_QUANTUM = Decimal("0.00000001")


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
    return list(
        session.scalars(
            select(LedgerEntryRow).order_by(LedgerEntryRow.created_at, LedgerEntryRow.id)
        )
    )


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
    token = snapshot.address
    row = session.scalar(
        select(MarketSnapshotRow).where(
            MarketSnapshotRow.chain == snapshot.chain,
            MarketSnapshotRow.token_address == token,
        )
    )
    source = snapshot.source
    observed = _aware(source.observed_at) if source is not None else None
    observed = observed or utcnow()
    if row is not None:
        current = _aware(row.observed_at)
        if current is not None and current > observed:
            return row
    if row is None:
        row = MarketSnapshotRow(id=new_id(), chain=snapshot.chain, token_address=token)
        session.add(row)
    row.symbol = snapshot.symbol or token
    row.name = snapshot.name or snapshot.symbol or token
    row.price_usd = snapshot.price_usd
    row.liquidity_usd = snapshot.liquidity_usd
    row.volume_24h_usd = snapshot.volume_24h_usd
    row.price_change_m5 = snapshot.price_change_m5
    row.price_change_h1 = snapshot.price_change_h1
    row.price_change_h6 = snapshot.price_change_h6
    row.price_change_h24 = snapshot.price_change_h24
    row.source = source.name if source is not None else "unknown"
    row.observed_at = observed
    row.raw = {}
    session.flush()
    return row


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
            source=None if snapshot is None else _source_ref(snapshot),
            mode="paper",
        )
        views.append(view.to_dict())
    views.sort(key=lambda item: (item["chain"], item["token_address"]))
    return views


def positions(session: Session) -> list[dict[str, Any]]:
    return _position_views(_replay(_ledger_rows(session)), _snapshot_index(session))


def account_snapshot(session: Session, starting_cash_usd: Decimal | None = None) -> dict[str, Any]:
    """Cash, realized, unrealized and equity projected from the ledger.

    ``starting_cash_usd`` is optional so bootstrapping callers can create genesis before
    reading. Marks come from the latest market snapshot. A position without a mark
    reports ``mark_usd = null`` and contributes nothing to the account totals; it is
    never silently valued at zero.
    """
    if starting_cash_usd is not None:
        ensure_genesis(session, starting_cash_usd)
    replay = _replay(_ledger_rows(session))
    views = _position_views(replay, _snapshot_index(session))
    known_market_value = Decimal(0)
    unrealized = Decimal(0)
    for view in views:
        market_value = view["market_value_usd"]
        if market_value is not None:
            known_market_value += Decimal(str(market_value))
            unrealized += Decimal(str(view["unrealized_usd"]))
    return {
        "cash_usd": str(replay.cash),
        "equity_usd": str(replay.cash + known_market_value),
        "realized_usd": str(replay.realized),
        "unrealized_usd": str(unrealized),
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


def limits_from_settings(settings: Settings) -> RiskLimits:
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


def _fill_fee(notional: Decimal, quote: Quote, settings: Settings) -> Decimal:
    """The fill's fee: the larger of the platform fee and the quote's own cost.

    A ``paper_mark`` carries no venue cost, so the platform's flat paper fee is the whole
    fee. An executable quote already pays a pool/aggregator fee and gas, reported as
    ``quote.fee_usd``: charging the bps fee *on top* would bill the same liquidity twice,
    and charging only the bps fee would hide a 0.25% LI.FI fee or three dollars of gas.
    The maximum of the two is never less than either honest number and never their sum.
    """
    platform = bps_fee(notional, settings.paper_fee_bps)
    if quote.kind == PAPER_MARK:
        return platform
    return max(platform, quote.fee_usd)


def _find_order(session: Session, idempotency_key: str) -> PaperOrderRow | None:
    return session.scalar(
        select(PaperOrderRow).where(PaperOrderRow.idempotency_key == idempotency_key)
    )


def _claim_order(session: Session, payload: OrderRequest) -> PaperOrderRow | None:
    """Insert the order row to claim the idempotency key. None means another won."""
    order = PaperOrderRow(
        id=new_id(),
        idempotency_key=payload.idempotency_key,
        chain=payload.chain,
        token_address=payload.token_address,
        side=payload.side,
        amount_usd=payload.amount_usd,
        status="pending",
        risk_profile=payload.risk_profile,
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
        return order, True
    existing = _find_order(session, payload.idempotency_key)
    if existing is None:  # pragma: no cover - the unique index guarantees a row exists
        raise RuntimeError("idempotency claim lost without an existing order")
    return existing, False


def _reject_new(
    session: Session,
    payload: OrderRequest,
    reason: str,
    snapshot: TokenSnapshot | None = None,
) -> PaperOrderRow:
    """Persist a rejection before a quote exists (insufficient position, unknown mark)."""
    order, created = _claim_or_existing(session, payload)
    if not created:
        return order
    if snapshot is not None:
        record_snapshot(session, snapshot)
    order.status = "rejected"
    order.reason = reason
    session.commit()
    return order


def _reject_after_quote(
    session: Session,
    payload: OrderRequest,
    reason: str,
    *,
    snapshot: TokenSnapshot,
    quote: Quote,
) -> PaperOrderRow:
    """Persist a rejection that has a quote, so the refused size stays replayable."""
    order, created = _claim_or_existing(session, payload)
    if not created:
        return order
    record_snapshot(session, snapshot)
    order.status = "rejected"
    order.reason = reason
    order.quote_kind = quote.kind
    order.quote_json = quote.to_dict()
    session.commit()
    return order


def _mark_rejected(session: Session, order: PaperOrderRow, reason: str) -> PaperOrderRow:
    order.status = "rejected"
    order.reason = reason
    session.commit()
    return order


def _attach_quote_price(snapshot: TokenSnapshot, quote: Quote) -> TokenSnapshot:
    """Use the executable quote price when the market feed has no price for the token."""
    if snapshot.price_usd is not None or quote.price <= 0:
        return snapshot
    return replace(snapshot, price_usd=quote.price, source=quote.source)


def _sized_sell(
    payload: OrderRequest, snapshot: TokenSnapshot, held_qty: Decimal
) -> Decimal | None:
    mark = snapshot.price_usd
    if mark is None or mark <= 0:
        return None
    return min(payload.amount_usd / mark, held_qty)


def _reject_manual_size(
    session: Session,
    *,
    chain: str,
    token_address: str,
    side: Side,
    amount_usd: Decimal | None,
    risk_profile: str | None,
    idempotency_key: str,
    reason: str,
) -> PaperOrderRow:
    """Persist a size refusal before any quote. The requested amount is never resized.

    A refusal has no resolved notional, so an omitted automatic amount is stored as
    ``0`` and the reason code is the authoritative record that no size was derived.
    """
    return _reject_new(
        session,
        OrderRequest(
            chain=chain,
            token_address=token_address,
            side=side,
            amount_usd=Decimal(0) if amount_usd is None else amount_usd,
            idempotency_key=idempotency_key,
            risk_profile=risk_profile,
        ),
        reason,
    )


async def place_manual_order(
    session: Session,
    *,
    chain: str,
    token_address: str,
    side: Side,
    amount_usd: Decimal | None,
    risk_profile: str | None,
    idempotency_key: str,
    settings: Settings,
    dexscreener: DexScreenerClient,
    quotes: QuoteRouter,
) -> PaperOrderRow:
    """Resolve the manual order's size before any quote, then place it.

    A buy with no amount is sized from the persisted policy and *server* equity. A
    refusal is persisted as a rejected order before the quote exists, so there is no
    silent default and no quote/fill to explain away. A sell is an exit: it requires an
    explicit amount and keeps the existing rule that size limits never trap a position.
    """
    ensure_genesis(session, settings.paper_starting_cash_usd)
    existing = _find_order(session, idempotency_key)
    if existing is not None:
        return existing

    if side == "sell":
        if amount_usd is None or amount_usd <= 0:
            return _reject_manual_size(
                session,
                chain=chain,
                token_address=token_address,
                side="sell",
                amount_usd=amount_usd,
                risk_profile=None,
                idempotency_key=idempotency_key,
                reason="AMOUNT_REQUIRED",
            )
        return await place_order(
            session,
            OrderRequest(
                chain=chain,
                token_address=token_address,
                side="sell",
                amount_usd=amount_usd,
                idempotency_key=idempotency_key,
                risk_profile=None,
            ),
            settings,
            dexscreener,
            quotes,
        )

    # Local import avoids the paper -> account_read -> paper import cycle at module load.
    from .account_read import equity_usd as account_equity_usd

    record = trade_policy.load_trade_policy(session, settings)
    resolution = trade_policy.quantize_resolution(
        trade_policy.resolve_order_size(
            equity_usd=account_equity_usd(session),
            record=record,
            profile=risk_profile,
            manual_amount_usd=amount_usd,
        )
    )
    if not resolution.ok or resolution.amount_usd is None:
        return _reject_manual_size(
            session,
            chain=chain,
            token_address=token_address,
            side="buy",
            amount_usd=amount_usd,
            # An unknown profile has no resolvable name; store NULL rather than an
            # arbitrary client string in a column reserved for stable profile names.
            risk_profile=resolution.profile,
            idempotency_key=idempotency_key,
            reason=resolution.reason or "SIZE_UNKNOWN",
        )

    # The resolver's amount is already the order notional at cent precision; it was
    # re-checked against the same limits after quantization. Nothing resizes it again.
    return await place_order(
        session,
        OrderRequest(
            chain=chain,
            token_address=token_address,
            side="buy",
            amount_usd=resolution.amount_usd,
            idempotency_key=idempotency_key,
            risk_profile=resolution.profile,
        ),
        settings,
        dexscreener,
        quotes,
    )


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
    ensure_genesis(session, settings.paper_starting_cash_usd)
    existing = _find_order(session, payload.idempotency_key)
    if existing is not None:
        return existing

    # Defence in depth for callers that bypass KillSwitchExecutor: an order that claims
    # strategy attribution is refused before any quote while the operator switch is armed.
    if payload.strategy_id is not None and read_kill_switch(session, settings).active:
        return _reject_new(session, payload, "KILL_SWITCH_ACTIVE")

    account = position_account(payload.chain, payload.token_address)
    held_qty = Decimal(0)
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
        quantity = _sized_sell(payload, snapshot, held_qty)
        if quantity is None or quantity <= 0:
            return _reject_new(session, payload, "PRICE_UNKNOWN", snapshot=snapshot)
        assert snapshot.price_usd is not None
        quote = await quotes.quote(
            chain=payload.chain,
            token_address=payload.token_address,
            side="sell",
            amount_usd=quantity * snapshot.price_usd,
            quantity=quantity,
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

        if payload.side == "sell":
            if held_qty <= 0:
                return _reject_after_quote(
                    session, payload, "INSUFFICIENT_POSITION", snapshot=snapshot, quote=quote
                )
            # Never fill a quantity that another writer already spent. A smaller quote is
            # fine (the position grew); a larger quote is stale and must be re-priced.
            if quote.amount_in > held_qty + MONEY_QUANTUM:
                return _reject_after_quote(
                    session, payload, "POSITION_CHANGED", snapshot=snapshot, quote=quote
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
            order.quote_json = quote.to_dict()
            return _mark_rejected(session, order, "NO_ROUTE")

        position_mark = snapshot.price_usd if snapshot.price_usd is not None else quote.price
        request = OrderRequest(
            chain=payload.chain,
            token_address=payload.token_address,
            side=payload.side,
            amount_usd=notional,
            idempotency_key=payload.idempotency_key,
            strategy_id=payload.strategy_id,
        )
        allowed, reasons = evaluate_order(
            request=request,
            snapshot=snapshot,
            quote=quote,
            current_position_value_usd=held_qty * position_mark,
            open_positions=_open_positions(replay),
            daily_realized_pnl_usd=replay.daily_realized,
            limits=limits_from_settings(settings),
        )
        fee = _fill_fee(notional, quote, settings)
        if allowed and payload.side == "buy" and replay.cash < notional + fee:
            allowed = False
            reasons = ("INSUFFICIENT_CASH",)

        if not allowed:
            order.quote_kind = quote.kind
            order.quote_json = quote.to_dict()
            return _mark_rejected(session, order, ",".join(reasons))

        stamp = utcnow()
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
        order.quote_json = quote.to_dict()
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
