"""PAPER performance for one strategy, from its own fills only.

Attribution is the join from a fill to its order and then to ``orders.strategy_id``. A
fill belonging to another strategy or to a manual order is never included, and there is
no fallback that spreads the account's PnL across strategies.

The curve is a cumulative realized PnL after fees at each of the strategy's fills. Buy
fees are recognized when paid; a position's unrealized PnL is reported only when a mark
is known, otherwise ``None`` (CONTRACT I3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import MarketSnapshotRow, PaperFillRow, PaperOrderRow, StrategyRow

HUNDRED = Decimal(100)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _owned_fills(session: Session, strategy_id: str) -> list[tuple[PaperFillRow, PaperOrderRow]]:
    rows = session.execute(
        select(PaperFillRow, PaperOrderRow)
        .join(PaperOrderRow, PaperFillRow.order_id == PaperOrderRow.id)
        .where(PaperOrderRow.strategy_id == strategy_id)
        .order_by(PaperFillRow.created_at, PaperFillRow.id)
    ).all()
    return [(fill, order) for fill, order in rows]


def strategy_performance(session: Session, strategy_id: str) -> dict[str, Any] | None:
    strategy = session.get(StrategyRow, strategy_id)
    if strategy is None:
        return None

    pairs = _owned_fills(session, strategy_id)
    quantity = Decimal(0)
    cost = Decimal(0)
    realized = Decimal(0)
    fees = Decimal(0)
    trades = 0
    wins = 0
    losses = 0
    cumulative = Decimal(0)
    curve: list[dict[str, str]] = []

    for fill, _order in pairs:
        fill_quantity = Decimal(str(fill.quantity))
        notional = Decimal(str(fill.notional_usd))
        fee = Decimal(str(fill.fee_usd))
        fees += fee
        if fill.side == "buy":
            quantity += fill_quantity
            cost += notional + fee
            cumulative -= fee
        else:
            closed = min(fill_quantity, quantity)
            if closed > 0 and quantity > 0:
                average = cost / quantity
                basis = average * closed
                pnl = notional - basis - fee
                realized += pnl
                trades += 1
                if pnl > 0:
                    wins += 1
                elif pnl < 0:
                    losses += 1
                cumulative += pnl
                quantity -= closed
                cost -= basis
                if quantity <= 0:
                    quantity = Decimal(0)
                    cost = Decimal(0)
            else:
                cumulative -= fee
        curve.append({"t": _aware(fill.created_at).isoformat(), "pnl_usd": str(cumulative)})

    snapshot = session.scalar(
        select(MarketSnapshotRow).where(
            MarketSnapshotRow.chain == strategy.chain,
            MarketSnapshotRow.token_address == strategy.token_address,
        )
    )
    mark = None if snapshot is None else snapshot.price_usd
    if quantity <= 0:
        unrealized: Decimal | None = Decimal(0)
    elif mark is None:
        unrealized = None
    else:
        unrealized = quantity * Decimal(str(mark)) - cost

    win_rate = None if trades == 0 else Decimal(wins) / Decimal(trades) * HUNDRED
    return {
        "strategy_id": strategy.id,
        "label": "PAPER",
        "realized_pnl_usd": str(realized),
        "unrealized_pnl_usd": None if unrealized is None else str(unrealized),
        "fees_usd": str(fees),
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": None if win_rate is None else str(win_rate),
        "open_quantity": str(quantity),
        "avg_cost_usd": None if quantity <= 0 else str(cost / quantity),
        "mark_usd": None if mark is None else str(mark),
        "equity_curve": curve,
    }


__all__ = ["strategy_performance"]
