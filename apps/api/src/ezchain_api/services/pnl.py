"""The one PnL projector for account and strategy performance.

Both read models call :func:`project` with rows from ``ledger_entries``: the account
passes every group, a strategy passes the group ids attributed to it. Fee legs are read
from the ledger once, a buy fee is realized when it is paid, and a sell's result is its
proceeds less the ledger's cost basis and fee. No second basis or fee formula exists.

The projector function is pure: callers supply ledger lines and a mark table, and
:func:`project` neither queries nor writes. The ``ledger_lines`` and ``mark_index``
helpers below only read. An open position without a mark reports ``None`` for market
value and unrealized PnL and makes the projection incomplete; it is never valued at zero
(CONTRACT I3). A zero-quantity position is genuinely flat, so its contribution is zero.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import LedgerEntryRow, MarketSnapshotRow

POSITION_PREFIX = "position:"
CASH_ACCOUNT = "cash:USD"
FEE_ACCOUNT = "fees:USD"
REALIZED_ACCOUNT = "pnl:realized:USD"
ZERO = Decimal(0)
WIN_RATE_QUANTUM = Decimal("0.01")

TokenKey = tuple[str, str]


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def split_position_account(account: str) -> TokenKey:
    chain, _, token = account[len(POSITION_PREFIX) :].partition(":")
    return chain, token


@dataclass(frozen=True)
class LedgerLine:
    """One ledger row as the projector sees it: no session, no ORM identity."""

    group_id: str
    account: str
    amount: Decimal
    usd_value: Decimal
    created_at: datetime | None


def lines(rows: Iterable[LedgerEntryRow]) -> list[LedgerLine]:
    """Convert persisted ledger rows without a float or a second decimal conversion."""
    return [
        LedgerLine(
            group_id=row.group_id,
            account=row.account,
            amount=Decimal(str(row.amount)),
            usd_value=Decimal(str(row.usd_value)),
            created_at=row.created_at,
        )
        for row in rows
    ]


def ledger_lines(session: Session) -> list[LedgerLine]:
    """Every ledger entry in journal order. The only input order the projector accepts."""
    return lines(session.scalars(select(LedgerEntryRow).order_by(LedgerEntryRow.seq)))


@dataclass(frozen=True)
class Mark:
    """The latest known price for a token, and the symbol it was observed under."""

    price_usd: Decimal | None
    symbol: str


def mark_index(session: Session) -> dict[TokenKey, Mark]:
    """One mark per (chain, token) from the same table for every read model."""
    index: dict[TokenKey, Mark] = {}
    for row in session.scalars(select(MarketSnapshotRow)):
        index[(row.chain, row.token_address)] = Mark(
            price_usd=None if row.price_usd is None else Decimal(str(row.price_usd)),
            symbol=row.symbol or row.token_address,
        )
    return index


def prices(index: Mapping[TokenKey, Mark]) -> dict[TokenKey, Decimal | None]:
    """The price half of a mark index, for :func:`project`."""
    return {token: mark.price_usd for token, mark in index.items()}


@dataclass(frozen=True)
class GroupProjection:
    """One order journal: its token, fee leg and net result after fees."""

    group_id: str
    token: TokenKey | None
    observed_at: datetime | None
    realized_usd: Decimal
    fees_usd: Decimal
    closed: bool
    quantity_delta: Decimal


@dataclass(frozen=True)
class PositionProjection:
    """One position account replayed from the selected ledger groups."""

    chain: str
    token_address: str
    quantity: Decimal
    cost_basis_usd: Decimal
    realized_usd: Decimal
    mark_usd: Decimal | None
    market_value_usd: Decimal | None
    unrealized_usd: Decimal | None

    @property
    def is_open(self) -> bool:
        return self.quantity > 0

    @property
    def avg_cost_usd(self) -> Decimal | None:
        if self.quantity <= 0:
            return None
        return self.cost_basis_usd / self.quantity


@dataclass(frozen=True)
class Projection:
    """A complete or explicitly incomplete PnL projection of one ledger selection."""

    cash_usd: Decimal
    realized_usd: Decimal
    fees_usd: Decimal
    groups: tuple[GroupProjection, ...]
    positions: tuple[PositionProjection, ...]
    complete: bool

    @property
    def marks_unknown(self) -> bool:
        return any(position.is_open and position.mark_usd is None for position in self.positions)

    @property
    def open_positions(self) -> tuple[PositionProjection, ...]:
        return tuple(position for position in self.positions if position.is_open)

    @property
    def unrealized_usd(self) -> Decimal | None:
        if not self.complete:
            return None
        total = ZERO
        for position in self.open_positions:
            value = position.unrealized_usd
            if value is None:  # pragma: no cover - complete already proved every mark
                return None
            total += value
        return total

    @property
    def market_value_usd(self) -> Decimal | None:
        if not self.complete:
            return None
        total = ZERO
        for position in self.open_positions:
            value = position.market_value_usd
            if value is None:  # pragma: no cover - complete already proved every mark
                return None
            total += value
        return total

    @property
    def equity_usd(self) -> Decimal | None:
        market_value = self.market_value_usd
        return None if market_value is None else self.cash_usd + market_value

    @property
    def wins(self) -> int:
        return sum(1 for group in self.groups if group.closed and group.realized_usd > 0)

    @property
    def losses(self) -> int:
        return sum(1 for group in self.groups if group.closed and group.realized_usd < 0)

    def position(self, token: TokenKey) -> PositionProjection | None:
        for position in self.positions:
            if (position.chain, position.token_address) == token:
                return position
        return None


@dataclass
class _WorkingGroup:
    group_id: str
    observed_at: datetime | None = None
    token: TokenKey | None = None
    cash_delta: Decimal = ZERO
    position_delta: Decimal = ZERO
    quantity_delta: Decimal = ZERO
    fees_usd: Decimal = ZERO
    has_position: bool = False
    closed: bool = False


def project(
    entries: Iterable[LedgerLine],
    *,
    marks: Mapping[TokenKey, Decimal | None],
    group_ids: AbstractSet[str] | None = None,
) -> Projection:
    """Project cash, positions and PnL from ledger lines.

    ``group_ids`` scopes the projection to one strategy's order journals while applying
    exactly the same arithmetic. ``None`` means every group (the account view). A scoped
    view can be internally oversold when a strategy shares a token with a manual order;
    that selection is reported incomplete rather than truncated into a made-up position.
    """
    groups: dict[str, _WorkingGroup] = {}
    cash = ZERO
    fees = ZERO
    quantities: dict[TokenKey, Decimal] = {}
    costs: dict[TokenKey, Decimal] = {}

    for line in entries:
        if group_ids is not None and line.group_id not in group_ids:
            continue
        group = groups.get(line.group_id)
        if group is None:
            group = _WorkingGroup(line.group_id)
            groups[line.group_id] = group
        stamp = _aware(line.created_at)
        if stamp is not None and (group.observed_at is None or stamp < group.observed_at):
            group.observed_at = stamp

        if line.account == CASH_ACCOUNT:
            cash += line.usd_value
            group.cash_delta += line.usd_value
        elif line.account == FEE_ACCOUNT:
            fees += line.usd_value
            group.fees_usd += line.usd_value
        elif line.account == REALIZED_ACCOUNT:
            group.closed = True
        elif line.account.startswith(POSITION_PREFIX):
            token = split_position_account(line.account)
            group.token = token
            group.has_position = True
            group.position_delta += line.usd_value
            group.quantity_delta += line.amount
            quantities[token] = quantities.get(token, ZERO) + line.amount
            costs[token] = costs.get(token, ZERO) + line.usd_value

    realized_by_token: dict[TokenKey, Decimal] = {}
    projected_groups: list[GroupProjection] = []
    for working in groups.values():
        realized = ZERO
        if working.has_position:
            # The group's cash delta plus its position delta is its trade result. On a
            # buy that is -fee; on a sell it is proceeds - cost basis - fee.
            realized = working.cash_delta + working.position_delta
            if working.token is not None:
                realized_by_token[working.token] = (
                    realized_by_token.get(working.token, ZERO) + realized
                )
        projected_groups.append(
            GroupProjection(
                group_id=working.group_id,
                token=working.token,
                observed_at=working.observed_at,
                realized_usd=realized,
                fees_usd=working.fees_usd,
                closed=working.closed and working.token is not None,
                quantity_delta=working.quantity_delta,
            )
        )

    positions: list[PositionProjection] = []
    realized_total = ZERO
    complete = True
    for token in sorted(quantities):
        quantity = quantities[token]
        cost = ZERO if quantity == 0 else costs[token]
        realized = realized_by_token.get(token, ZERO)
        realized_total += realized
        mark = marks.get(token)
        if quantity > 0:
            if mark is None:
                market_value = None
                unrealized = None
                complete = False
            else:
                market_value = quantity * mark
                unrealized = market_value - cost
        elif quantity == 0:
            market_value = ZERO
            unrealized = ZERO
        else:
            # Only a scoped selection can legitimately reach this, because the account
            # ledger forbids a negative position. The scoped view withholds its value
            # instead of inventing a close; the account view raises below.
            market_value = None
            unrealized = None
            complete = False
        positions.append(
            PositionProjection(
                chain=token[0],
                token_address=token[1],
                quantity=quantity,
                cost_basis_usd=cost,
                realized_usd=realized,
                mark_usd=mark,
                market_value_usd=market_value,
                unrealized_usd=unrealized,
            )
        )

    if group_ids is None:
        for position in positions:
            if position.quantity < 0:
                # The whole account cannot be oversold; a negative row is ledger
                # corruption, not a projection to serve (CONTRACT I1/I3).
                raise ValueError("position quantity cannot be negative")

    return Projection(
        cash_usd=cash,
        realized_usd=realized_total,
        fees_usd=fees,
        groups=tuple(projected_groups),
        positions=tuple(positions),
        complete=complete,
    )


def num(value: Decimal) -> str:
    """Canonical decimal string: no exponent and no float round-trip."""
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def win_rate_pct(wins: int, losses: int) -> str | None:
    """Wins over decided closes, or None when nothing has a direction."""
    decided = wins + losses
    if decided == 0:
        return None
    rate = (Decimal(wins) * 100 / Decimal(decided)).quantize(WIN_RATE_QUANTUM)
    return num(rate)


def _aware_local(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _window_cutoff(window: Any, now: datetime) -> tuple[datetime | None, str]:
    """Return (cutoff, normalized window label). None cutoff means all history."""
    if window is None:
        return None, "all"
    text = str(window).strip().lower()
    if text in ("", "all", "none"):
        return None, "all"
    hours: float | None = None
    if text.endswith("h"):
        try:
            hours = float(text[:-1])
        except ValueError:
            hours = None
    elif text.endswith("d"):
        try:
            hours = float(text[:-1]) * 24
        except ValueError:
            hours = None
    else:
        try:
            hours = float(text)
        except ValueError:
            hours = None
    if hours is None or hours <= 0:
        return None, "all"
    return now - timedelta(hours=hours), f"{hours:g}h"


def performance_series(
    entries: Iterable[LedgerLine],
    *,
    marks: Mapping[TokenKey, Decimal | None],
    group_ids: AbstractSet[str] | None = None,
    window: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Honest equity / realized PnL / drawdown series from ledger groups.

    Points are one per journal group in journal order; no interpolation or filler.
    Values are cumulative projections of the ledger rows through that group, using the
    same projector as the account and strategy reads. ``window`` bounds which points
    are returned; cumulative values still reflect all prior groups.
    """
    if now is None:
        now = datetime.now(UTC)
    cutoff, window_label = _window_cutoff(window, now)

    by_group: dict[str, list[LedgerLine]] = {}
    for line in entries:
        if group_ids is not None and line.group_id not in group_ids:
            continue
        by_group.setdefault(line.group_id, []).append(line)

    def observed(lines_for_group: list[LedgerLine]) -> datetime | None:
        stamps: list[datetime] = []
        for line in lines_for_group:
            stamp = _aware_local(line.created_at)
            if stamp is not None:
                stamps.append(stamp)
        return min(stamps) if stamps else None

    # Preserve ledger/seq order: the projector consumes ledger rows in seq order,
    # so the series must follow the same order rather than re-sorting by the
    # observed timestamp, which can disagree with journal order.
    ordered = list(by_group.items())

    cumulative: list[LedgerLine] = []
    equity_points: list[dict[str, Any]] = []
    realized_points: list[dict[str, Any]] = []
    last_projection: Projection | None = None

    for _group_id, group_lines in ordered:
        group_time = observed(group_lines) or now
        cumulative.extend(group_lines)
        projection = project(cumulative, marks=marks, group_ids=None)
        last_projection = projection

        if cutoff is not None and group_time < cutoff:
            continue

        t = group_time.isoformat()
        equity_value = projection.equity_usd
        realized_value = projection.realized_usd
        equity_points.append(
            {
                "t": t,
                "timestamp": t,
                "value": None if equity_value is None else num(equity_value),
                "equity_usd": None if equity_value is None else num(equity_value),
            }
        )
        realized_points.append(
            {
                "t": t,
                "timestamp": t,
                "value": num(realized_value),
                "realized_pnl_usd": num(realized_value),
            }
        )

    drawdown_points: list[dict[str, Any]] = []
    peak: Decimal | None = None
    for point in equity_points:
        value = point["value"]
        t = point["t"]
        if value is None:
            drawdown_points.append({"t": t, "timestamp": t, "value": None, "drawdown_pct": None})
            continue
        numeric = Decimal(value)
        if peak is None or numeric > peak:
            peak = numeric
        drawdown = Decimal(0) if peak is None or peak <= 0 else (numeric / peak - 1) * 100
        drawdown_points.append(
            {
                "t": t,
                "timestamp": t,
                "value": num(drawdown),
                "drawdown_pct": num(drawdown),
            }
        )

    if last_projection is None:
        complete = True
        marks_unknown = False
    else:
        complete = last_projection.complete
        marks_unknown = last_projection.marks_unknown

    if not equity_points and not realized_points:
        reason = "NO_SERIES_DATA"
    elif marks_unknown:
        reason = "MARKS_UNKNOWN"
    else:
        reason = None

    return {
        "window": window_label,
        "source": "ledger",
        "complete": complete,
        "marks_unknown": marks_unknown,
        "reason": reason,
        "equity_series": equity_points,
        "realized_pnl_series": realized_points,
        "drawdown_series": drawdown_points,
    }


__all__ = [
    "GroupProjection",
    "LedgerLine",
    "Mark",
    "PositionProjection",
    "Projection",
    "TokenKey",
    "ledger_lines",
    "lines",
    "mark_index",
    "num",
    "performance_series",
    "prices",
    "project",
    "split_position_account",
    "win_rate_pct",
]
