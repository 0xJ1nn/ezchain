"""Read-only account projections from the append-only paper ledger.

No function here writes. Cash, positions and PnL are replayed from ``ledger_entries``
(CONTRACT I1); fills and orders are read only for counts and rejection alerts. A value
that cannot be proven from persisted rows stays ``None`` rather than becoming zero
(CONTRACT I3). Paper numbers are always labelled ``paper`` (CONTRACT I12).
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_core import utcnow
from ezchain_core.ledger import fee_account, realized_account

from ..config import Settings
from ..models import LedgerEntryRow, MarketSnapshotRow, PaperFillRow, PaperOrderRow
from . import paper, reconciliation
from .system_flags import read_kill_switch

POSITION_PREFIX = "position:"
ZERO = Decimal(0)
WIN_RATE_QUANTUM = Decimal("0.01")
SHARE_QUANTUM = Decimal("0.01")
REJECTION_WINDOW = timedelta(hours=24)
EXPORT_LIMIT = 5000
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _num(value: Decimal) -> str:
    """Canonical decimal string: no exponent and no float round-trip."""
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _dt(value: datetime | None) -> str | None:
    aware = _aware(value)
    return None if aware is None else aware.isoformat()


def _split_position_account(account: str) -> tuple[str, str]:
    chain, _, token = account[len(POSITION_PREFIX) :].partition(":")
    return chain, token


# ---------------------------------------------------------------------------
# Ledger projection
# ---------------------------------------------------------------------------


def ledger(
    session: Session,
    *,
    limit: int = 200,
    account: str | None = None,
    kind: str | None = None,
) -> list[dict[str, Any]]:
    """Newest-first journal slice. Filters are exact so a caller cannot invent a query."""
    query = select(LedgerEntryRow).order_by(
        LedgerEntryRow.created_at.desc(), LedgerEntryRow.id.desc()
    )
    if account is not None:
        query = query.where(LedgerEntryRow.account == account)
    if kind is not None:
        query = query.where(LedgerEntryRow.kind == kind)
    rows = session.scalars(query.limit(limit)).all()
    return [
        {
            "id": row.id,
            "group_id": row.group_id,
            "account": row.account,
            "asset": row.asset,
            "amount": _num(Decimal(str(row.amount))),
            "usd_value": _num(Decimal(str(row.usd_value))),
            "kind": row.kind,
            "created_at": _dt(row.created_at),
        }
        for row in rows
    ]


@dataclass(frozen=True)
class _Group:
    """One order journal: its token, fee leg, net result and whether it closed a trade."""

    group_id: str
    observed_at: datetime | None
    token: tuple[str, str] | None
    fee_usd: Decimal
    realized_usd: Decimal
    closed: bool


def _groups(rows: list[LedgerEntryRow]) -> list[_Group]:
    grouped: dict[str, list[LedgerEntryRow]] = {}
    for row in rows:
        grouped.setdefault(row.group_id, []).append(row)

    groups: list[_Group] = []
    for group_id, group in grouped.items():
        token: tuple[str, str] | None = None
        fee = ZERO
        closed = False
        for row in group:
            if row.account.startswith(POSITION_PREFIX):
                token = _split_position_account(row.account)
            elif row.account == fee_account():
                fee += Decimal(str(row.usd_value))
            elif row.account == realized_account():
                # A sell books a pnl leg; a buy does not. That is the provable close.
                closed = True
        stamps = [_aware(row.created_at) for row in group]
        observed = min((stamp for stamp in stamps if stamp is not None), default=None)
        groups.append(
            _Group(
                group_id=group_id,
                observed_at=observed,
                token=token,
                fee_usd=fee,
                realized_usd=paper.group_position_delta(group),
                closed=closed and token is not None,
            )
        )
    return groups


@dataclass(frozen=True)
class _TokenBook:
    chain: str
    token_address: str
    symbol: str
    realized_usd: Decimal
    unrealized_usd: Decimal | None
    fees_usd: Decimal
    trades: int
    quantity: Decimal
    avg_cost_usd: Decimal | None
    mark_usd: Decimal | None
    market_value_usd: Decimal | None


def _snapshots(session: Session) -> dict[tuple[str, str], MarketSnapshotRow]:
    rows = session.scalars(select(MarketSnapshotRow)).all()
    return {(row.chain, row.token_address): row for row in rows}


def _token_books(
    replay: paper.Replay,
    snapshots: dict[tuple[str, str], MarketSnapshotRow],
    groups: list[_Group],
    fills: list[PaperFillRow],
) -> list[_TokenBook]:
    fees: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for group in groups:
        if group.token is not None:
            fees[group.token] += group.fee_usd
    trades = Counter((fill.chain, fill.token_address) for fill in fills)

    books: list[_TokenBook] = []
    for account, (quantity, cost) in replay.positions.items():
        chain, token = _split_position_account(account)
        snapshot = snapshots.get((chain, token))
        mark = None if snapshot is None else snapshot.price_usd
        symbol = snapshot.symbol if snapshot is not None and snapshot.symbol else token
        if mark is None:
            market_value = ZERO if quantity == 0 else None
            unrealized = ZERO if quantity == 0 else None
        else:
            market_value = quantity * Decimal(str(mark))
            unrealized = market_value - cost
        books.append(
            _TokenBook(
                chain=chain,
                token_address=token,
                symbol=symbol,
                realized_usd=replay.realized_by_account.get(account, ZERO),
                unrealized_usd=unrealized,
                fees_usd=fees.get((chain, token), ZERO),
                trades=trades.get((chain, token), 0),
                quantity=quantity,
                avg_cost_usd=None if quantity == 0 else cost / quantity,
                mark_usd=None if mark is None else Decimal(str(mark)),
                market_value_usd=market_value,
            )
        )
    books.sort(key=lambda book: (book.chain, book.token_address))
    return books


def _book_dict(book: _TokenBook) -> dict[str, Any]:
    return {
        "chain": book.chain,
        "token_address": book.token_address,
        "symbol": book.symbol,
        "realized_usd": _num(book.realized_usd),
        "unrealized_usd": None if book.unrealized_usd is None else _num(book.unrealized_usd),
        "fees_usd": _num(book.fees_usd),
        "trades": book.trades,
        "quantity": _num(book.quantity),
        "avg_cost_usd": None if book.avg_cost_usd is None else _num(book.avg_cost_usd),
        "mark_usd": None if book.mark_usd is None else _num(book.mark_usd),
        "market_value_usd": (
            None if book.market_value_usd is None else _num(book.market_value_usd)
        ),
    }


def _win_rate(wins: int, losses: int) -> str | None:
    decided = wins + losses
    if decided == 0:
        return None
    rate = (Decimal(wins) * 100 / Decimal(decided)).quantize(WIN_RATE_QUANTUM)
    return _num(rate)


def _daily_realized(groups: list[_Group], fallback: datetime) -> list[dict[str, Any]]:
    totals: dict[str, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    for group in groups:
        if group.token is None:
            continue
        observed = group.observed_at or fallback
        day = observed.astimezone(UTC).date().isoformat()
        totals[day][0] += group.realized_usd
        totals[day][1] += group.fee_usd
    return [
        {"date": day, "realized_usd": _num(values[0]), "fees_usd": _num(values[1])}
        for day, values in sorted(totals.items())
    ]


def _ledger_view(
    session: Session,
) -> tuple[paper.Replay, list[_Group], list[PaperFillRow], list[_TokenBook]]:
    rows = paper.ledger_rows(session)
    replay = paper.project_ledger(rows)
    groups = _groups(rows)
    fills = list(session.scalars(select(PaperFillRow).order_by(PaperFillRow.created_at)))
    books = _token_books(replay, _snapshots(session), groups, fills)
    return replay, groups, fills, books


# ---------------------------------------------------------------------------
# Performance and attribution
# ---------------------------------------------------------------------------


def performance(session: Session) -> dict[str, Any]:
    now = utcnow()
    replay, groups, fills, books = _ledger_view(session)

    open_books = [book for book in books if book.quantity > 0]
    # One missing mark makes the portfolio total unknowable. Do not value it at zero.
    marks_unknown = any(book.mark_usd is None for book in open_books)
    unrealized = (
        None if marks_unknown else sum((book.unrealized_usd or ZERO for book in open_books), ZERO)
    )
    market_value = sum((book.market_value_usd or ZERO for book in open_books), ZERO)
    equity = None if marks_unknown else replay.cash + market_value

    closed = [group for group in groups if group.closed]
    wins = sum(1 for group in closed if group.realized_usd > 0)
    losses = sum(1 for group in closed if group.realized_usd < 0)
    fees = sum((group.fee_usd for group in groups), ZERO)

    return {
        "mode": "paper",
        "as_of": now.astimezone(UTC).isoformat(),
        "cash_usd": _num(replay.cash),
        "equity_usd": None if equity is None else _num(equity),
        "realized_usd": _num(replay.realized),
        "unrealized_usd": None if unrealized is None else _num(unrealized),
        "fees_usd": _num(fees),
        "trades": len(fills),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": _win_rate(wins, losses),
        "open_positions": len(open_books),
        "by_token": [_book_dict(book) for book in books],
        "daily_realized": _daily_realized(groups, now),
    }


def equity_usd(session: Session) -> Decimal | None:
    """Server equity for sizing, or ``None`` when an open mark is unknown.

    ``performance`` already applies the fail-closed rule: one missing mark makes the
    portfolio total unknowable. Sizing must see that same null, never a total that
    silently values the unknown position at zero.
    """
    value = performance(session)["equity_usd"]
    return None if value is None else Decimal(str(value))


def attribution(session: Session) -> list[dict[str, Any]]:
    _, _, _, books = _ledger_view(session)
    total_abs = sum((abs(book.realized_usd) for book in books), ZERO)
    rows: list[dict[str, Any]] = []
    for book in books:
        share = None
        if total_abs != 0:
            share = _num((abs(book.realized_usd) * 100 / total_abs).quantize(SHARE_QUANTUM))
        row = _book_dict(book)
        row["share_pct"] = share
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Limits and alerts
# ---------------------------------------------------------------------------


def limits(session: Session, settings: Settings) -> dict[str, Any]:
    """The UI-visible risk limits plus the persisted operator switch. No secrets."""
    state = read_kill_switch(session, settings)
    return {
        "mode": "paper",
        "max_order_usd": _num(settings.max_order_usd),
        "max_position_usd": _num(settings.max_position_usd),
        "max_open_positions": settings.max_open_positions,
        "max_daily_loss_usd": _num(settings.max_daily_loss_usd),
        "min_pair_age_minutes": settings.min_pair_age_minutes,
        "min_liquidity_usd": _num(settings.min_liquidity_usd),
        "paper_mark_max_order_usd": _num(settings.paper_mark_max_order_usd),
        "paper_fee_bps": settings.paper_fee_bps,
        "max_quote_age_seconds": settings.max_quote_age_seconds,
        "kill_switch": state.to_dict(),
    }


def _alert(
    alert_id: str,
    code: str,
    severity: str,
    title: str,
    detail: str,
    created_at: str,
    resource: str,
) -> dict[str, Any]:
    return {
        "id": alert_id,
        "code": code,
        "severity": severity,
        "title": title,
        "detail": detail,
        "created_at": created_at,
        "resource": resource,
    }


def _mark_alerts(
    session: Session, settings: Settings, now: datetime, as_of: str
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    for view in paper.positions(session):
        chain = str(view["chain"])
        token = str(view["token_address"])
        source = view.get("source")
        observed = source.get("observed_at") if isinstance(source, dict) else None
        stamp = _aware(datetime.fromisoformat(observed) if isinstance(observed, str) else None)
        resource = f"position:{chain}:{token}"
        created = None if stamp is None else stamp.isoformat()
        if view["mark_usd"] is None:
            alerts.append(
                _alert(
                    f"mark.unknown.{chain}.{token}",
                    "POSITION_MARK_UNKNOWN",
                    "warning",
                    "Position has no known mark",
                    f"{view['symbol']} on {chain} is open but no price is known; "
                    "equity and unrealized PnL stay null.",
                    created or as_of,
                    resource,
                )
            )
            continue
        if stamp is None:
            alerts.append(
                _alert(
                    f"mark.unknown.{chain}.{token}",
                    "POSITION_MARK_UNKNOWN",
                    "warning",
                    "Position mark has no observation time",
                    f"{view['symbol']} on {chain} has a price but no source freshness.",
                    as_of,
                    resource,
                )
            )
            continue
        age = (now - stamp).total_seconds()
        if age > settings.max_quote_age_seconds:
            alerts.append(
                _alert(
                    f"mark.stale.{chain}.{token}",
                    "POSITION_MARK_STALE",
                    "warning",
                    "Position mark is stale",
                    f"{view['symbol']} on {chain} was last observed {int(age)}s ago; "
                    f"the order gate accepts at most {settings.max_quote_age_seconds}s.",
                    created or as_of,
                    resource,
                )
            )
    return alerts


def _rejection_alerts(session: Session, now: datetime, as_of: str) -> list[dict[str, Any]]:
    cutoff = now - REJECTION_WINDOW
    rows = session.scalars(
        select(PaperOrderRow)
        .where(PaperOrderRow.status == "rejected", PaperOrderRow.created_at >= cutoff)
        .order_by(PaperOrderRow.created_at)
    ).all()
    grouped: dict[str, list[PaperOrderRow]] = defaultdict(list)
    for row in rows:
        grouped[row.reason or "UNKNOWN"].append(row)

    alerts: list[dict[str, Any]] = []
    for reason, orders in sorted(grouped.items()):
        digest = hashlib.sha256(reason.encode("utf-8")).hexdigest()[:12]
        latest: datetime | None = None
        for order in orders:
            stamp = _aware(order.created_at)
            if stamp is not None and (latest is None or stamp > latest):
                latest = stamp
        alerts.append(
            _alert(
                f"orders.rejected.{digest}",
                "ORDERS_REJECTED",
                "info",
                f"{len(orders)} rejected paper order(s)",
                f"Reason {reason} in the last 24h ({len(orders)} order(s)).",
                (latest or now).isoformat(),
                "paper_orders",
            )
        )
    return alerts


def alerts(session: Session, settings: Settings) -> dict[str, Any]:
    """Derived alert feed. There is no alerts table and nothing is repaired here."""
    now = utcnow()
    as_of = now.astimezone(UTC).isoformat()
    report = reconciliation.reconciliation(session)
    totals = report["totals"]
    items: list[dict[str, Any]] = []

    if not report["ok"]:
        failed = [check["id"] for check in report["checks"] if not check["ok"]]
        items.append(
            _alert(
                "reconciliation.failed",
                "RECONCILIATION_FAILED",
                "critical",
                "Ledger reconciliation failed",
                f"{len(failed)} check(s) failed: {', '.join(failed)}.",
                str(report["as_of"]),
                "reconciliation",
            )
        )
    if int(totals["negative_cash"]) > 0:
        items.append(
            _alert(
                "cash.negative",
                "NEGATIVE_CASH",
                "critical",
                "Cash account is negative",
                f"cash:USD sums to {totals['cash_usd']}; the ledger says the account "
                "spent more than it holds.",
                str(report["as_of"]),
                "ledger",
            )
        )
    if int(totals["negative_positions"]) > 0:
        items.append(
            _alert(
                "positions.negative",
                "NEGATIVE_POSITION",
                "critical",
                "A position quantity is negative",
                f"{totals['negative_positions']} position account(s) hold a negative quantity.",
                str(report["as_of"]),
                "ledger",
            )
        )

    kill = read_kill_switch(session, settings)
    if kill.active:
        state = kill.to_dict()
        items.append(
            _alert(
                "kill_switch.active",
                "KILL_SWITCH_ACTIVE",
                "warning",
                "Kill switch is armed",
                f"Strategy-scoped orders are blocked. Reason: {kill.reason or 'none given'}.",
                str(state["updated_at"] or as_of),
                "kill_switch",
            )
        )

    items.extend(_mark_alerts(session, settings, now, as_of))
    items.extend(_rejection_alerts(session, now, as_of))

    items.sort(key=lambda item: (SEVERITY_ORDER[str(item["severity"])], str(item["id"])))
    return {"as_of": as_of, "alerts": items}


# ---------------------------------------------------------------------------
# CSV exports
# ---------------------------------------------------------------------------


def export_orders(session: Session) -> list[dict[str, Any]]:
    rows = paper.orders(session, EXPORT_LIMIT)
    return [
        {
            "id": row.id,
            "chain": row.chain,
            "token_address": row.token_address,
            "side": row.side,
            "amount_usd": str(row.amount_usd),
            "status": row.status,
            "reason": row.reason,
            "quote_kind": row.quote_kind,
            "created_at": _dt(row.created_at),
            "filled_at": _dt(row.filled_at),
        }
        for row in rows
    ]


def export_fills(session: Session) -> list[dict[str, Any]]:
    rows = paper.fills(session, EXPORT_LIMIT)
    return [
        {
            "id": row.id,
            "order_id": row.order_id,
            "chain": row.chain,
            "token_address": row.token_address,
            "side": row.side,
            "quantity": str(row.quantity),
            "price_usd": str(row.price_usd),
            "notional_usd": str(row.notional_usd),
            "fee_usd": str(row.fee_usd),
            "quote_kind": row.quote_kind,
            "created_at": _dt(row.created_at),
        }
        for row in rows
    ]


def export_ledger(session: Session) -> list[dict[str, Any]]:
    return ledger(session, limit=EXPORT_LIMIT)
