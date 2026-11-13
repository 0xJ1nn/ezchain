"""PAPER performance for one strategy, projected from the account ledger.

Attribution is the join from a fill to its order and then to ``orders.strategy_id``. A
fill belonging to another strategy or to a manual order is never included, and there is
no fallback that spreads the account's PnL across strategies. The selected ledger groups
go through :mod:`ezchain_api.services.pnl`, the same projector account performance calls,
so a fee is booked once and the same rows cannot produce two numbers.

A buy fee is realized when the ledger pays it, not capitalised into the remaining cost
basis. A position's market value and unrealized PnL are reported only when a mark is
known; otherwise they are null and ``complete`` is false (CONTRACT I3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import PaperFillRow, PaperOrderRow, StrategyRow
from . import pnl

ZERO = Decimal(0)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _owned_group_ids(session: Session, strategy_id: str) -> set[str]:
    """Ledger group ids (order ids) whose fill is attributed to this strategy."""
    orders = session.scalars(
        select(PaperOrderRow.id)
        .join(PaperFillRow, PaperFillRow.order_id == PaperOrderRow.id)
        .where(PaperOrderRow.strategy_id == strategy_id)
    )
    return set(orders)


def strategy_performance(
    session: Session, strategy_id: str, *, window: Any = None
) -> dict[str, Any] | None:
    strategy = session.get(StrategyRow, strategy_id)
    if strategy is None:
        return None

    now = datetime.now(UTC)
    marks = pnl.mark_index(
        session,
        max_age_seconds=get_settings().max_mark_age_seconds,
        now=now,
    )
    group_ids = _owned_group_ids(session, strategy_id)
    projection = pnl.project(
        pnl.ledger_lines(session),
        marks=pnl.prices(marks),
        group_ids=group_ids,
    )
    target = (strategy.chain, strategy.token_address)
    position = projection.position(target)
    mark = marks.get(target)
    if position is not None and position.quantity < 0:
        # A strategy that sold more than its own attributed buys cannot have a position;
        # the selection is reported incomplete without inventing a truncation.
        open_quantity: Decimal | None = None
    else:
        open_quantity = ZERO if position is None else position.quantity

    series = pnl.performance_series(
        pnl.ledger_lines(session),
        marks=pnl.prices(marks),
        group_ids=group_ids,
        window=window,
        now=now,
    )
    curve = [{"t": p["t"], "pnl_usd": p["realized_pnl_usd"]} for p in series["realized_pnl_series"]]

    return {
        "strategy_id": strategy.id,
        "label": "PAPER",
        "realized_pnl_usd": pnl.num(projection.realized_usd),
        "unrealized_pnl_usd": (
            None if projection.unrealized_usd is None else pnl.num(projection.unrealized_usd)
        ),
        "fees_usd": pnl.num(projection.fees_usd),
        "trades": sum(1 for group in projection.groups if group.closed),
        "wins": projection.wins,
        "losses": projection.losses,
        "win_rate_pct": pnl.win_rate_pct(projection.wins, projection.losses),
        "open_quantity": None if open_quantity is None else pnl.num(open_quantity),
        "avg_cost_usd": (
            None
            if position is None or position.avg_cost_usd is None
            else pnl.num(position.avg_cost_usd)
        ),
        "mark_usd": (None if mark is None or mark.price_usd is None else pnl.num(mark.price_usd)),
        "market_value_usd": (
            None if projection.market_value_usd is None else pnl.num(projection.market_value_usd)
        ),
        "complete": projection.complete,
        "marks_unknown": projection.marks_unknown,
        "equity_curve": curve,
        "window": series["window"],
        "source": series["source"],
        "reason": series["reason"],
        "equity_series": series["equity_series"],
        "realized_pnl_series": series["realized_pnl_series"],
        "drawdown_series": series["drawdown_series"],
        "series": {
            "equity": series["equity_series"],
            "realized_pnl": series["realized_pnl_series"],
            "drawdown": series["drawdown_series"],
        },
    }


__all__ = ["strategy_performance"]
