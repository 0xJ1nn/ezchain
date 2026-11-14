"""Async strategy runtime: fetch, derive, decide, execute, log.

The decision itself is pure (``ezchain_core.strategy``). This module adds the side
effects: market snapshot, ledger-derived position, one execution per interval and an
append-only run log. Paused strategies never reach the order executor.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol, cast, runtime_checkable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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

    async def get_token(
        self, chain: str, address: str, *, max_age_seconds: float | None = None
    ) -> TokenSnapshot: ...


@runtime_checkable
class OrderExecutor(Protocol):
    """The paper order service, viewed through the single execution path.

    ``place_order`` returns the persisted order id and is expected to honour the
    idempotency key, so a retry after a crash cannot double-fill.
    """

    async def place_order(self, request: OrderRequest) -> str: ...


class KillSwitchExecutor:
    """Decorate an order executor with the persisted operator kill switch.

    This is the strategy level of the switch: it only inspects orders that claim
    strategy attribution. The order service enforces the global level, which refuses
    every buy whoever placed it (ADR-0034), so a manual order that passes here is still
    refused there. The check runs per order, so arming takes effect immediately and
    survives a restart without restarting the process. The guard holds no locks, so it is
    safe across processes.
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
    # ``None`` when this call did not decide: the run log carries no resolved size, and an
    # unknown amount is not zero (CONTRACT I3).
    amount_usd: Decimal | None
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
        .order_by(LedgerEntryRow.seq)
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


def _window_bucket(now: datetime, interval: int) -> int:
    """The interval bucket a decision taken at ``now`` belongs to."""
    return int(now.timestamp()) // (max(1, interval) * 60)


def decision_window(strategy_id: str, now: datetime, interval: int) -> str:
    """Name the one decision a strategy may take in this interval window.

    Stored on the run row under a unique ``(strategy_id, decision_window)`` pair, so the
    insert itself is the claim: two evaluators that both passed the interval gate cannot
    both log the window. It deliberately ignores the action and the amount, because a
    window carries one decision -- an evaluator that reached a different one is still the
    loser of the same claim.
    """
    return f"strategy:{strategy_id}:{_window_bucket(now, interval)}"


def idempotency_key(
    strategy_id: str,
    now: datetime,
    interval: int,
    action: str | None = None,
    amount_usd: Any = None,
) -> str:
    """Stable within one decision window, distinct across windows.

    Bound to the action and resolved amount when supplied.
    """
    bucket = _window_bucket(now, interval)
    if action is not None or amount_usd is not None:
        return f"strategy:{strategy_id}:{action or ''}:{_canonical_amount(amount_usd)}:{bucket}"
    return f"strategy:{strategy_id}:{bucket}"


def _canonical_amount(value: Any) -> str:
    """One spelling per amount, so 10, 10.0 and 10.00 name the same decision."""
    if value is None:
        return ""
    try:
        text = format(Decimal(str(value)), "f")
    except Exception:
        return str(value)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _record(
    session: Session,
    strategy: StrategyRow,
    decision: StrategyDecision,
    *,
    order_id: str | None = None,
) -> StrategyRunRow | None:
    """Append a decision that does not claim an execution window.

    Holds that consumed nothing -- an interval that has not elapsed, a market read that
    failed -- leave ``decision_window`` NULL, so a retry inside the window stays possible
    and two of them cannot collide.
    """
    row = StrategyRunRow(
        strategy_id=strategy.id,
        action=decision.action,
        reason=decision.reason,
        inputs=decision.inputs,
        order_id=order_id,
    )
    session.add(row)
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


def _claim_window(
    session: Session,
    strategy: StrategyRow,
    decision: StrategyDecision,
    *,
    now: datetime,
    window: str,
) -> tuple[StrategyRunRow | None, bool]:
    """Claim ``window`` for this decision, or return the row that already holds it.

    The claim is one insert under the unique ``(strategy_id, decision_window)`` pair, and
    ``last_run_at`` moves in the same transaction: a claim that did not commit consumed
    nothing. It runs *before* any execution, so the caller that loses the window never
    reaches the order service -- that is what makes one window one execution rather than
    one window one surviving order.

    ``(row, True)`` means this call owns the window and still has to complete the row.
    ``(winner, False)`` means another evaluator owns it; the row is that winner's record.
    ``(None, False)`` means the strategy was deleted before the claim could be recorded.
    """
    row = StrategyRunRow(
        strategy_id=strategy.id,
        action=decision.action,
        reason=decision.reason,
        inputs=decision.inputs,
        decision_window=window,
    )
    session.add(row)
    strategy.last_run_at = now
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        # The rolled-back session may no longer hold the strategy row, so the id is read
        # from the row this call built rather than from an object after a rollback.
        strategy_id = row.strategy_id
        winner = session.scalar(
            select(StrategyRunRow).where(
                StrategyRunRow.strategy_id == strategy_id,
                StrategyRunRow.decision_window == window,
            )
        )
        if winner is None:
            logger.warning("strategy %s disappeared before its run was recorded", strategy_id)
            return None, False
        logger.info(
            "strategy %s already decided window %s; run %s wins", strategy_id, window, winner.id
        )
        return winner, False
    session.refresh(row)
    return row, True


def _complete_window(session: Session, row: StrategyRunRow, *, order_id: str) -> None:
    """Record the order the claimed decision produced, on the claim's own row."""
    row.order_id = order_id
    session.commit()
    session.refresh(row)


def _release_window(
    session: Session,
    strategy: StrategyRow,
    row: StrategyRunRow,
    decision: StrategyDecision,
    *,
    order_id: str | None,
    previous_last_run: datetime | None,
) -> StrategyRunRow:
    """Turn the claim into the fail-closed hold it became, and free the window again.

    A refusal is not a decision: nothing filled, so the row stops claiming the window and
    ``last_run_at`` goes back to what it was. The row keeps the refusal that is now the
    truth -- never the ``buy``/``sell`` it attempted -- and releasing the cause retries
    immediately instead of waiting one interval.
    """
    row.action = decision.action
    row.reason = decision.reason
    row.inputs = decision.inputs
    row.order_id = order_id
    row.decision_window = None
    strategy.last_run_at = previous_last_run
    session.commit()
    session.refresh(row)
    return row


def _result(
    strategy_id: str,
    decision: StrategyDecision,
    row: StrategyRunRow | None,
    *,
    owned: bool = True,
) -> RunResult:
    """Report what the log holds, which is the winner's record when this call lost.

    A caller that lost the window did not decide it, so its own decision is not what the
    run log says. The row carries no resolved size, so the loser reports no amount at all
    rather than attaching its own arithmetic to another evaluator's decision.

    The id is passed in rather than read from the strategy: a rollback expires the row the
    session loaded, and a strategy deleted mid-evaluation would raise instead of reporting
    that the decision has no record.
    """
    if row is None:
        return RunResult(
            strategy_id=strategy_id,
            action=decision.action,
            reason=decision.reason,
            amount_usd=decision.amount_usd,
            inputs=decision.inputs,
        )
    return RunResult(
        strategy_id=strategy_id,
        action=row.action,
        reason=row.reason,
        amount_usd=decision.amount_usd if owned else None,
        inputs=row.inputs or {},
        run_id=row.id,
        order_id=row.order_id,
        created_at=row.created_at,
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
    max_mark_age_seconds: float | None = None,
    force: bool = False,
) -> RunResult:
    """Run one strategy once.

    Order of gates: paused → valid params → interval → snapshot → position → decision →
    window claim → executor → run log. Paused strategies are skipped without a run row and
    without touching ``last_run_at``; an interval that has not elapsed is logged as a hold
    so a manual run is still auditable.

    A decision that consumes the interval claims its window first, and only the claim's
    owner executes: one window yields one run row and one execution, and a caller that
    loses returns the winner's record. A decision that produces no execution (a wait, or a
    refusal that fails closed) still claims its window when it is the window's decision;
    a refusal releases the claim and restores ``last_run_at``, so releasing its cause
    retries immediately.

    ``force`` marks an explicit owner request rather than a scheduled tick: it runs the
    cycle even when the strategy is paused and even inside its interval. The row's status
    is not changed, so the scheduler keeps skipping a paused strategy afterwards. It does
    not override the window claim: a forced run inside a window that already holds a
    decision reports that decision instead of making a second one.

    ``max_mark_age_seconds`` is the mark budget every engine read carries, so an old
    observation is flagged ``stale`` instead of arriving as a constant ``False``. The
    composition root wires it from the same setting the API uses; ``None`` keeps the
    adapter's unbudgeted behaviour and is only for a caller that does not price risk.
    """
    now = now or utcnow()
    # Captured before anything can roll back: the claim reads it after a failed insert,
    # and the row it came from may be expired or deleted by then.
    strategy_id = strategy.id
    if strategy.status != "active" and not force:
        return RunResult(
            strategy_id=strategy_id,
            action="hold",
            reason="PAUSED",
            amount_usd=Decimal(0),
            inputs={"status": strategy.status},
        )

    graph_vars: dict[str, str | None] | None = None
    if strategy.kind == "graph":
        graph_vars = serialize_graph_vars(latest_graph_vars(session, strategy_id))

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
        # This hold consumes the cooldown the scheduler falls back to for an unreadable
        # interval (``_due``), so it claims that window like any other decision: two ticks
        # must not both record one unreadable strategy.
        row, _owns = _claim_window(
            session,
            strategy,
            decision,
            now=now,
            window=decision_window(strategy_id, now, DEFAULT_INTERVAL_MINUTES),
        )
        return _result(strategy_id, decision, row)

    last_run_at = strategy.last_run_at
    if not force and not should_run(last_run_at, now, interval):
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
        row = _record(session, strategy, decision)
        return _result(strategy_id, decision, row)

    try:
        snapshot = await dexscreener.get_token(
            strategy.chain, strategy.token_address, max_age_seconds=max_mark_age_seconds
        )
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
        row = _record(session, strategy, decision)
        return _result(strategy_id, decision, row)

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
            row = _record(session, strategy, decision)
            return _result(strategy_id, decision, row)
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
            row = _record(session, strategy, decision)
            return _result(strategy_id, decision, row)
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
            row = _record(session, strategy, decision)
            return _result(strategy_id, decision, row)
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
            row = _record(session, strategy, decision)
            return _result(strategy_id, decision, row)
    else:
        position = position_state(
            session, strategy.chain, strategy.token_address, strategy.token_symbol
        )
        spec = spec_from_row(strategy)
        if force:
            # The pure evaluator re-reads the pause flag and its own interval from the
            # spec, so an explicit manual run is presented as active and due for this
            # one call. The persisted row is untouched and keeps the strategy paused.
            spec = replace(spec, status="active", last_run_at=None)
        decision = evaluate_plan(
            spec,
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
            idempotency_key=idempotency_key(
                strategy.id, now, interval, action=decision.action, amount_usd=decision.amount_usd
            ),
            strategy_id=strategy.id,
            # The strategy's own trade-policy profile, resolved by the order gate exactly
            # as a person's order is. Null inherits the deployment default, so a strategy
            # without an override keeps today's behaviour, and an override can never be a
            # second resolver: the same minimum, maximum and percent-of-equity gates apply.
            risk_profile=strategy.risk_profile,
        )
        # Claim before executing, never after: the claim is what makes one window one
        # execution. An evaluator that completes its order first and then loses the run
        # insert has already spent money on a decision the log will not carry.
        row, owns = _claim_window(
            session, strategy, decision, now=now, window=decision_window(strategy.id, now, interval)
        )
        if row is None or not owns:
            return _result(strategy_id, decision, row, owned=False)
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
            held = _release_window(
                session,
                strategy,
                row,
                blocked,
                order_id=None,
                previous_last_run=last_run_at,
            )
            return _result(strategy_id, blocked, held)
        except OrderRejected as exc:
            logger.warning(
                "strategy %s order %s rejected: %s", strategy.id, exc.order_id, exc.reason
            )
            rejected = StrategyDecision(
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
            )
            held = _release_window(
                session,
                strategy,
                row,
                rejected,
                order_id=exc.order_id,
                previous_last_run=last_run_at,
            )
            return _result(strategy_id, rejected, held)
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
            held = _release_window(
                session,
                strategy,
                row,
                failed,
                order_id=None,
                previous_last_run=last_run_at,
            )
            return _result(strategy_id, failed, held)
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
            held = _release_window(
                session,
                strategy,
                row,
                failed,
                order_id=None,
                previous_last_run=last_run_at,
            )
            return _result(strategy_id, failed, held)

        _complete_window(session, row, order_id=order_id)
        return _result(strategy_id, decision, row)

    # A decision that consumes the interval claims its window even when it spends
    # nothing: a wait is the window's decision, so two ticks cannot each log one.
    row, _owns = _claim_window(
        session, strategy, decision, now=now, window=decision_window(strategy.id, now, interval)
    )
    return _result(strategy_id, decision, row)


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
        max_mark_age_seconds: float | None = None,
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
        self._max_mark_age_seconds = max_mark_age_seconds
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
                            max_mark_age_seconds=self._max_mark_age_seconds,
                        )
                    )
                except Exception:
                    logger.exception("strategy %s evaluation crashed", strategy_id)
        # Protection arms share this tick and this intent path. Evaluate them
        # after strategies so a strategy close wins the position race and an
        # arm then sees NO_POSITION instead of double-selling.
        try:
            from ezchain_engine.protection import evaluate_all_arms

            await evaluate_all_arms(
                self._session_factory,
                self._dexscreener,
                self._order_executor,
                now=now,
                max_mark_age_seconds=self._max_mark_age_seconds,
            )
        except Exception:
            logger.exception("protection pass failed")
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
