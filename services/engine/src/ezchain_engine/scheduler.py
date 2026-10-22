"""Async strategy runtime: fetch, derive, decide, execute, log.

The decision itself is pure (``ezchain_core.strategy``). This module adds the side
effects: market snapshot, ledger-derived position, one execution per interval and an
append-only run log. Paused strategies never reach the order executor.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol, cast, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_api.models import LedgerEntryRow, StrategyRow, StrategyRunRow
from ezchain_core.ledger import LedgerEntry, position_account, replay_position
from ezchain_core.strategy import (
    DEFAULT_INTERVAL_MINUTES,
    StrategyDecision,
    StrategySpec,
    StrategyValidationError,
    interval_minutes,
)
from ezchain_core.strategy import evaluate_strategy as evaluate_plan
from ezchain_core.types import OrderRequest, PositionState, StrategyKind, TokenSnapshot, utcnow

from .engine import should_run

logger = logging.getLogger(__name__)


class ExecutorUnavailable(RuntimeError):
    """Raised by composition when no paper order service has been wired yet."""


@runtime_checkable
class TokenSource(Protocol):
    """The only market-data call the engine needs."""

    async def get_token(self, chain: str, address: str) -> TokenSnapshot: ...


@runtime_checkable
class OrderExecutor(Protocol):
    """The paper order service, viewed through the single execution path.

    ``place_order`` returns the persisted order id and is expected to honour the
    idempotency key, so a retry after a crash cannot double-fill.
    """

    async def place_order(self, request: OrderRequest) -> str: ...


@dataclass(frozen=True)
class RunResult:
    """What one scheduler/manual invocation did."""

    strategy_id: str
    action: str
    reason: str
    amount_usd: Decimal
    inputs: dict[str, Any]
    run_id: str | None = None
    order_id: str | None = None
    created_at: datetime | None = None

    @property
    def logged(self) -> bool:
        return self.run_id is not None


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def spec_from_row(strategy: StrategyRow) -> StrategySpec:
    return StrategySpec(
        id=strategy.id,
        name=strategy.name,
        chain=strategy.chain,
        token_address=strategy.token_address,
        token_symbol=strategy.token_symbol,
        kind=cast(StrategyKind, strategy.kind),
        params=dict(strategy.params or {}),
        status=strategy.status,
        last_run_at=strategy.last_run_at,
    )


def position_state(
    session: Session, chain: str, token_address: str, symbol: str = ""
) -> PositionState:
    """Replay this position's ledger entries. There is no mutable balance scalar."""
    rows = session.scalars(
        select(LedgerEntryRow)
        .where(LedgerEntryRow.account == position_account(chain, token_address))
        .order_by(LedgerEntryRow.created_at, LedgerEntryRow.id)
    ).all()
    entries = (
        LedgerEntry(
            account=row.account,
            asset=row.asset,
            amount=Decimal(str(row.amount)),
            usd_value=Decimal(str(row.usd_value)),
            kind=row.kind,
        )
        for row in rows
    )
    quantity, cost = replay_position(entries)
    return PositionState(
        chain=chain,
        token_address=token_address,
        symbol=symbol,
        quantity=quantity,
        cost_usd=cost,
    )


def idempotency_key(strategy_id: str, now: datetime, interval: int) -> str:
    """Stable within one decision window, distinct across windows."""
    window = max(1, interval) * 60
    bucket = int(now.timestamp()) // window
    return f"strategy:{strategy_id}:{bucket}"


def _record(
    session: Session,
    strategy: StrategyRow,
    decision: StrategyDecision,
    *,
    now: datetime,
    touch_last_run: bool,
    order_id: str | None = None,
) -> StrategyRunRow:
    row = StrategyRunRow(
        strategy_id=strategy.id,
        action=decision.action,
        reason=decision.reason,
        inputs=decision.inputs,
        order_id=order_id,
    )
    session.add(row)
    if touch_last_run:
        strategy.last_run_at = now
    session.commit()
    session.refresh(row)
    return row


def _result(
    strategy: StrategyRow, decision: StrategyDecision, row: StrategyRunRow | None
) -> RunResult:
    return RunResult(
        strategy_id=strategy.id,
        action=decision.action,
        reason=decision.reason,
        amount_usd=decision.amount_usd,
        inputs=decision.inputs,
        run_id=None if row is None else row.id,
        order_id=None if row is None else row.order_id,
        created_at=None if row is None else row.created_at,
    )


async def evaluate_strategy(
    session: Session,
    strategy: StrategyRow,
    dexscreener: TokenSource,
    order_executor: OrderExecutor,
    *,
    now: datetime | None = None,
) -> RunResult:
    """Run one strategy once.

    Order of gates: paused → valid params → interval → snapshot → position → decision →
    executor → run log. Paused strategies are skipped without a run row and without
    touching ``last_run_at``; an interval that has not elapsed is logged as a hold so a
    manual run is still auditable.
    """
    now = now or utcnow()
    if strategy.status != "active":
        return RunResult(
            strategy_id=strategy.id,
            action="hold",
            reason="PAUSED",
            amount_usd=Decimal(0),
            inputs={"status": strategy.status},
        )

    try:
        interval = interval_minutes(cast(StrategyKind, strategy.kind), dict(strategy.params or {}))
    except StrategyValidationError as exc:
        decision = StrategyDecision(
            "hold",
            Decimal(0),
            "INVALID_PARAMS",
            {"kind": strategy.kind, "error": exc.code, "message": exc.message},
        )
        row = _record(session, strategy, decision, now=now, touch_last_run=True)
        return _result(strategy, decision, row)

    last_run_at = strategy.last_run_at
    if not should_run(last_run_at, now, interval):
        elapsed = (
            None
            if last_run_at is None
            else (_aware(now) - _aware(last_run_at)).total_seconds() / 60
        )
        decision = StrategyDecision(
            "hold",
            Decimal(0),
            "INTERVAL_NOT_ELAPSED",
            {
                "kind": strategy.kind,
                "interval_minutes": interval,
                "minutes_since_last_run": elapsed,
            },
        )
        row = _record(session, strategy, decision, now=now, touch_last_run=False)
        return _result(strategy, decision, row)

    try:
        snapshot = await dexscreener.get_token(strategy.chain, strategy.token_address)
    except Exception as exc:  # fail closed: no snapshot, no order
        logger.warning("strategy %s market data unavailable: %s", strategy.id, type(exc).__name__)
        decision = StrategyDecision(
            "hold",
            Decimal(0),
            "MARKET_DATA_UNAVAILABLE",
            {
                "kind": strategy.kind,
                "chain": strategy.chain,
                "token_address": strategy.token_address,
                "error": type(exc).__name__,
            },
        )
        row = _record(session, strategy, decision, now=now, touch_last_run=False)
        return _result(strategy, decision, row)

    position = position_state(
        session, strategy.chain, strategy.token_address, strategy.token_symbol
    )
    decision = evaluate_plan(
        spec_from_row(strategy),
        snapshot,
        position_quantity=position.quantity,
        position_avg_cost_usd=position.avg_cost_usd,
        now=now,
    )

    order_id: str | None = None
    if decision.action in ("buy", "sell"):
        request = OrderRequest(
            chain=strategy.chain,
            token_address=strategy.token_address,
            side=decision.action,
            amount_usd=decision.amount_usd,
            idempotency_key=idempotency_key(strategy.id, now, interval),
            strategy_id=strategy.id,
        )
        try:
            order_id = await order_executor.place_order(request)
        except ExecutorUnavailable as exc:
            logger.warning("strategy %s cannot execute: %s", strategy.id, exc)
            failed = StrategyDecision(
                "hold",
                Decimal(0),
                "ORDER_EXECUTOR_UNAVAILABLE",
                {
                    **decision.inputs,
                    "attempted_action": decision.action,
                    "attempted_amount_usd": str(decision.amount_usd),
                },
            )
            row = _record(session, strategy, failed, now=now, touch_last_run=False)
            return _result(strategy, failed, row)
        except Exception as exc:  # fail closed; do not consume the window
            logger.warning("strategy %s order failed: %s", strategy.id, type(exc).__name__)
            failed = StrategyDecision(
                "hold",
                Decimal(0),
                "EXECUTION_FAILED",
                {
                    **decision.inputs,
                    "attempted_action": decision.action,
                    "attempted_amount_usd": str(decision.amount_usd),
                    "error": type(exc).__name__,
                },
            )
            row = _record(session, strategy, failed, now=now, touch_last_run=False)
            return _result(strategy, failed, row)

    row = _record(
        session,
        strategy,
        decision,
        now=now,
        touch_last_run=True,
        order_id=order_id,
    )
    return _result(strategy, decision, row)


def _due(strategy: StrategyRow, now: datetime) -> bool:
    """Invalid params fall back to the default cooldown instead of a hot retry loop."""
    try:
        interval = interval_minutes(cast(StrategyKind, strategy.kind), dict(strategy.params or {}))
    except StrategyValidationError:
        # Throttle legacy invalid rows instead of retrying on every scheduler tick.
        return should_run(strategy.last_run_at, now, DEFAULT_INTERVAL_MINUTES)
    return should_run(strategy.last_run_at, now, interval)


class StrategyScheduler:
    """Bounded asyncio loop. One session per strategy; one decision per interval."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        dexscreener: TokenSource,
        order_executor: OrderExecutor,
        *,
        interval_seconds: float = 30.0,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._dexscreener = dexscreener
        self._order_executor = order_executor
        self._interval_seconds = max(1.0, interval_seconds)
        self._now = now
        self._stop = asyncio.Event()

    async def run_once(self) -> list[RunResult]:
        """Evaluate every active, due strategy once.

        One failing strategy never stops the rest, and a strategy whose interval has not
        elapsed is not even opened: the run log records decisions, not scheduler ticks.
        """
        now = self._now()
        with self._session_factory() as session:
            ids = list(
                session.scalars(
                    select(StrategyRow.id)
                    .where(StrategyRow.status == "active")
                    .order_by(StrategyRow.created_at)
                ).all()
            )
        results: list[RunResult] = []
        for strategy_id in ids:
            with self._session_factory() as session:
                strategy = session.get(StrategyRow, strategy_id)
                if strategy is None or strategy.status != "active":
                    continue
                if not _due(strategy, now):
                    continue
                try:
                    results.append(
                        await evaluate_strategy(
                            session,
                            strategy,
                            self._dexscreener,
                            self._order_executor,
                            now=now,
                        )
                    )
                except Exception:
                    logger.exception("strategy %s evaluation crashed", strategy_id)
        return results

    async def run_forever(self) -> None:
        self._stop.clear()
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("scheduler pass failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                continue

    def stop(self) -> None:
        """Idempotent stop request; the loop exits after the current pass."""
        self._stop.set()


__all__ = [
    "OrderExecutor",
    "RunResult",
    "StrategyScheduler",
    "TokenSource",
    "evaluate_strategy",
    "idempotency_key",
    "position_state",
    "spec_from_row",
]
