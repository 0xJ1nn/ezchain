"""Strategy validation and manual evaluation for the API edge.

The engine owns the decision and the run log; this module only maps a persisted row to the
domain spec and supplies the composition seam for the paper order service.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy.orm import Session

from ezchain_core.strategy import validate_params
from ezchain_core.types import OrderRequest, StrategyKind
from ezchain_engine import (
    ExecutorUnavailable,
    OrderExecutor,
    RunResult,
    TokenSource,
    evaluate_strategy,
    spec_from_row,
)

from ..models import StrategyRow

__all__ = [
    "MissingOrderExecutor",
    "run_strategy_once",
    "spec_from_row",
    "validate_strategy_params",
]


def validate_strategy_params(kind: str, params: dict[str, Any] | None) -> None:
    """Validate before persisting, so a bad row never reaches the scheduler."""
    validate_params(cast(StrategyKind, kind), params)


class MissingOrderExecutor:
    """Fail-closed stand-in until the composition root wires the paper order service.

    A buy/sell decision is logged with ``ORDER_EXECUTOR_UNAVAILABLE``; nothing is filled.
    """

    async def place_order(self, request: OrderRequest) -> str:
        raise ExecutorUnavailable("paper order executor is not wired; set app.state.order_executor")


async def run_strategy_once(
    session: Session,
    strategy: StrategyRow,
    token_source: TokenSource,
    order_executor: OrderExecutor,
    *,
    now: datetime | None = None,
) -> RunResult:
    return await evaluate_strategy(session, strategy, token_source, order_executor, now=now)
