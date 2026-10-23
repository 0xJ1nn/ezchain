"""Paper-to-live readiness, computed from orders, backtests and reconciliation."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ezchain_core import GateEvidence, GateThresholds, evaluate_gate, utcnow

from ..config import Settings
from ..models import BacktestRow, PaperOrderRow, StrategyRow
from . import reconciliation


def _aware(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def live_readiness(
    session: Session,
    strategy: StrategyRow,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    moment = now or utcnow()
    intents = int(
        session.scalar(
            select(func.count())
            .select_from(PaperOrderRow)
            .where(PaperOrderRow.strategy_id == strategy.id)
        )
        or 0
    )
    fills = int(
        session.scalar(
            select(func.count())
            .select_from(PaperOrderRow)
            .where(
                PaperOrderRow.strategy_id == strategy.id,
                PaperOrderRow.status == "filled",
            )
        )
        or 0
    )
    backtests = int(
        session.scalar(
            select(func.count())
            .select_from(BacktestRow)
            .where(BacktestRow.strategy_id == strategy.id)
        )
        or 0
    )
    report = reconciliation.reconciliation(session)
    result = evaluate_gate(
        GateEvidence(
            started_at=_aware(strategy.created_at),
            now=moment,
            intents=intents,
            fills=fills,
            backtests=backtests,
            reconciliation_ok=bool(report["ok"]),
            invariant_breach=not bool(report["ok"]),
        ),
        GateThresholds(
            min_days=settings.paper_gate_min_days,
            min_intents=settings.paper_gate_min_intents,
            min_fills=settings.paper_gate_min_fills,
            require_backtest=settings.paper_gate_require_backtest,
        ),
    )
    return {
        "strategy_id": strategy.id,
        "mode": "paper",
        "live_available": False,
        "live_blocked_reason": "the live executor is not shipped; promotion cannot execute",
        **result.to_dict(),
    }


__all__ = ["live_readiness"]
