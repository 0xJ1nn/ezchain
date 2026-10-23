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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ezchain_api.models import LedgerEntryRow, PaperOrderRow, StrategyRow, StrategyRunRow
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
from .graph import (
    GraphContextBuilder,
    evaluate_graph_strategy,
    latest_graph_vars,
    serialize_graph_vars,
)
from .plugin_host import PluginHost
from .plugin_runtime import (
    PluginContextBuilder,
    evaluate_plugin_strategy,
)
from .plugins import PluginRegistry

logger = logging.getLogger(__name__)


class ExecutorUnavailable(RuntimeError):
    """Raised by composition when no paper order service has been wired yet."""


class ExecutionBlocked(RuntimeError):
    """Raised when an operator switch stops strategy-originated execution.

    The evaluation layer turns this into a logged ``hold`` without consuming the
    strategy's decision window, so releasing the switch does not wait one interval.
    """

    code = "KILL_SWITCH_ACTIVE"


class OrderRejected(RuntimeError):
    """Raised when the order service persisted a rejection instead of a fill.

    A strategy run must not record ``buy``/``sell`` for an order that never filled. The
    evaluation layer turns this into a ``hold`` / ``ORDER_REJECTED`` decision that keeps
    the attempted action and the rejected order id for diagnosis.
    """

    code = "ORDER_REJECTED"

    def __init__(self, reason: str | None, *, order_id: str) -> None:
        super().__init__(reason or self.code)
        self.reason = reason
        self.order_id = order_id


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


class KillSwitchExecutor:
    """Decorate an order executor with the persisted operator kill switch.

    The check runs per order, so arming takes effect immediately and survives a
    restart without restarting the process. Manual paper orders (``strategy_id``
    is ``None``) stay allowed: the switch scope is strategy-originated execution.
    The guard holds no locks, so it is safe across processes.
    """

    def __init__(self, inner: OrderExecutor, is_active: Callable[[], bool]) -> None:
        self._inner = inner
        self._is_active = is_active

    @property
    def active(self) -> bool:
        """True while the operator switch is armed; read fresh from persistence."""
        return self._is_active()

    async def place_order(self, request: OrderRequest) -> str:
        if request.strategy_id is not None and self._is_active():
            raise ExecutionBlocked("strategy execution is blocked by the operator kill switch")
        return await self._inner.place_order(request)


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
    # The order's persisted sizing proof, copied into the append-only run log.
    size_resolution: dict[str, Any] | None = None
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
        plugin_id=strategy.plugin_id,
        plugin_version=strategy.plugin_version,
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
    size_resolution: dict[str, Any] | None = None,
) -> StrategyRunRow | None:
    row = StrategyRunRow(
        strategy_id=strategy.id,
        action=decision.action,
        reason=decision.reason,
        inputs=decision.inputs,
        size_resolution=size_resolution,
        order_id=order_id,
    )
    session.add(row)
    if touch_last_run:
        strategy.last_run_at = now
    try:
        session.commit()
    except IntegrityError:
        # The strategy was deleted between load and insert. The decision cannot be
        # attributed any more, so drop it instead of breaking the scheduler pass.
        session.rollback()
        logger.warning("strategy %s disappeared before its run was recorded", strategy.id)
        return None
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
        size_resolution=None if row is None else row.size_resolution,
        created_at=None if row is None else row.created_at,
    )


def _order_size_resolution(session: Session, order_id: str | None) -> dict[str, Any] | None:
    """Read the order's sizing proof after the executor committed it.

    The executor uses its own session, so this is a fresh read under READ COMMITTED.
    A fake executor in a unit test has no row; the run then records no snapshot rather
    than inventing one. The value is already a JSON document (decimal strings).
    """
    if order_id is None:
        return None
    row = session.get(PaperOrderRow, order_id)
    if row is None or not isinstance(row.size_resolution, dict):
        return None
    return dict(row.size_resolution)


def _with_size_resolution(
    decision: StrategyDecision, size_resolution: dict[str, Any] | None
) -> StrategyDecision:
    """Put the snapshot in the decision inputs and keep the run's action unchanged."""
    if size_resolution is None:
        return decision
    return StrategyDecision(
        decision.action,
        decision.amount_usd,
        decision.reason,
        {**decision.inputs, "size_resolution": size_resolution},
    )


async def evaluate_strategy(
    session: Session,
    strategy: StrategyRow,
    dexscreener: TokenSource,
    order_executor: OrderExecutor,
    *,
    graph_runtime: GraphContextBuilder | None = None,
    plugin_runtime: PluginContextBuilder | None = None,
    plugin_host: PluginHost | None = None,
    plugin_registry: PluginRegistry | None = None,
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

    graph_vars: dict[str, str | None] | None = None
    if strategy.kind == "graph":
        graph_vars = serialize_graph_vars(latest_graph_vars(session, strategy.id))

    try:
        interval = interval_minutes(cast(StrategyKind, strategy.kind), dict(strategy.params or {}))
    except StrategyValidationError as exc:
        inputs: dict[str, Any] = {
            "kind": strategy.kind,
            "error": exc.code,
            "message": exc.message,
        }
        if graph_vars is not None:
            inputs["graph_vars"] = graph_vars
        decision = StrategyDecision("hold", Decimal(0), "INVALID_PARAMS", inputs)
        row = _record(session, strategy, decision, now=now, touch_last_run=True)
        return _result(strategy, decision, row)

    last_run_at = strategy.last_run_at
    if not should_run(last_run_at, now, interval):
        elapsed = (
            None
            if last_run_at is None
            else (_aware(now) - _aware(last_run_at)).total_seconds() / 60
        )
        inputs = {
            "kind": strategy.kind,
            "interval_minutes": interval,
            "minutes_since_last_run": elapsed,
        }
        if graph_vars is not None:
            inputs["graph_vars"] = graph_vars
        decision = StrategyDecision("hold", Decimal(0), "INTERVAL_NOT_ELAPSED", inputs)
        row = _record(session, strategy, decision, now=now, touch_last_run=False)
        return _result(strategy, decision, row)

    try:
        snapshot = await dexscreener.get_token(strategy.chain, strategy.token_address)
    except Exception as exc:  # fail closed: no snapshot, no order
        logger.warning("strategy %s market data unavailable: %s", strategy.id, type(exc).__name__)
        inputs = {
            "kind": strategy.kind,
            "chain": strategy.chain,
            "token_address": strategy.token_address,
            "error": type(exc).__name__,
        }
        if graph_vars is not None:
            inputs["graph_vars"] = graph_vars
        decision = StrategyDecision("hold", Decimal(0), "MARKET_DATA_UNAVAILABLE", inputs)
        row = _record(session, strategy, decision, now=now, touch_last_run=False)
        return _result(strategy, decision, row)

    if strategy.kind == "plugin":
        if plugin_runtime is None or plugin_host is None or plugin_registry is None:
            decision = StrategyDecision(
                "hold",
                Decimal(0),
                "PLUGIN_RUNTIME_UNAVAILABLE",
                {
                    "kind": "plugin",
                    "plugin_id": strategy.plugin_id,
                    "plugin_version": strategy.plugin_version,
                },
            )
            row = _record(session, strategy, decision, now=now, touch_last_run=False)
            return _result(strategy, decision, row)
        decision = await evaluate_plugin_strategy(
            session,
            strategy,
            snapshot,
            plugin_runtime,
            plugin_host,
            plugin_registry,
            now=now,
        )
    elif strategy.kind == "graph":
        if graph_runtime is None:
            decision = StrategyDecision(
                "hold",
                Decimal(0),
                "GRAPH_RUNTIME_UNAVAILABLE",
                {
                    "kind": "graph",
                    "chain": strategy.chain,
                    "token_address": strategy.token_address,
                    "graph_vars": graph_vars or {},
                },
            )
            row = _record(session, strategy, decision, now=now, touch_last_run=False)
            return _result(strategy, decision, row)
        try:
            decision = await evaluate_graph_strategy(
                session, strategy, snapshot, graph_runtime, now=now
            )
        except StrategyValidationError as exc:
            decision = StrategyDecision(
                "hold",
                Decimal(0),
                "INVALID_PARAMS",
                {
                    "kind": "graph",
                    "error": exc.code,
                    "message": exc.message,
                    "graph_vars": graph_vars or {},
                },
            )
            row = _record(session, strategy, decision, now=now, touch_last_run=False)
            return _result(strategy, decision, row)
        except Exception as exc:  # fail closed: no candles/ledger, no order
            logger.warning(
                "strategy %s graph context unavailable: %s", strategy.id, type(exc).__name__
            )
            decision = StrategyDecision(
                "hold",
                Decimal(0),
                "GRAPH_CONTEXT_UNAVAILABLE",
                {
                    "kind": "graph",
                    "chain": strategy.chain,
                    "token_address": strategy.token_address,
                    "error": type(exc).__name__,
                    "graph_vars": graph_vars or {},
                },
            )
            row = _record(session, strategy, decision, now=now, touch_last_run=False)
            return _result(strategy, decision, row)
    else:
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
        except ExecutionBlocked as exc:
            logger.warning("strategy %s execution blocked: %s", strategy.id, exc)
            blocked = StrategyDecision(
                "hold",
                Decimal(0),
                "KILL_SWITCH_ACTIVE",
                {
                    **decision.inputs,
                    "attempted_action": decision.action,
                    "attempted_amount_usd": str(decision.amount_usd),
                    "error_code": exc.code,
                },
            )
            row = _record(session, strategy, blocked, now=now, touch_last_run=False)
            return _result(strategy, blocked, row)
        except OrderRejected as exc:
            logger.warning(
                "strategy %s order %s rejected: %s", strategy.id, exc.order_id, exc.reason
            )
            size_resolution = _order_size_resolution(session, exc.order_id)
            rejected = _with_size_resolution(
                StrategyDecision(
                    "hold",
                    Decimal(0),
                    "ORDER_REJECTED",
                    {
                        **decision.inputs,
                        "attempted_action": decision.action,
                        "attempted_amount_usd": str(decision.amount_usd),
                        "order_id": exc.order_id,
                        "order_reason": exc.reason,
                        "error_code": exc.code,
                    },
                ),
                size_resolution,
            )
            row = _record(
                session,
                strategy,
                rejected,
                now=now,
                touch_last_run=False,
                order_id=exc.order_id,
                size_resolution=size_resolution,
            )
            return _result(strategy, rejected, row)
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

    size_resolution = _order_size_resolution(session, order_id)
    decision = _with_size_resolution(decision, size_resolution)
    row = _record(
        session,
        strategy,
        decision,
        now=now,
        touch_last_run=True,
        order_id=order_id,
        size_resolution=size_resolution,
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
        graph_runtime: GraphContextBuilder | None = None,
        plugin_runtime: PluginContextBuilder | None = None,
        plugin_host: PluginHost | None = None,
        plugin_registry: PluginRegistry | None = None,
        interval_seconds: float = 30.0,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._dexscreener = dexscreener
        self._order_executor = order_executor
        self._graph_runtime = graph_runtime
        self._plugin_runtime = plugin_runtime
        self._plugin_host = plugin_host
        self._plugin_registry = plugin_registry
        self._interval_seconds = max(1.0, interval_seconds)
        self._now = now
        self._stop = asyncio.Event()

    async def run_once(self) -> list[RunResult]:
        """Evaluate every active, due strategy once.

        One failing strategy never stops the rest, and a strategy whose interval has not
        elapsed is not even opened: the run log records decisions, not scheduler ticks.
        While the operator kill switch is armed the pass is skipped before any market
        read, so an idle outage does not write a hold row per strategy per tick.
        """
        if getattr(self._order_executor, "active", False):
            logger.warning("strategy scheduler idle: operator kill switch is active")
            return []
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
                            graph_runtime=self._graph_runtime,
                            plugin_runtime=self._plugin_runtime,
                            plugin_host=self._plugin_host,
                            plugin_registry=self._plugin_registry,
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
    "ExecutionBlocked",
    "ExecutorUnavailable",
    "GraphContextBuilder",
    "KillSwitchExecutor",
    "OrderExecutor",
    "OrderRejected",
    "RunResult",
    "StrategyScheduler",
    "TokenSource",
    "evaluate_strategy",
    "idempotency_key",
    "position_state",
    "spec_from_row",
]
