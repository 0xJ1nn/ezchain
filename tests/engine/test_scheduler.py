"""Scheduler integration tests: gates, run log, ledger-derived position, execution."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from helpers import CHAIN, TOKEN, make_snapshot
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

import ezchain_api.models  # noqa: F401
from ezchain_api.models import LedgerEntryRow, StrategyRow, StrategyRunRow
from ezchain_api.services.strategies import MissingOrderExecutor
from ezchain_core.ledger import position_account
from ezchain_core.types import OrderRequest, TokenSnapshot
from ezchain_engine import (
    ExecutionBlocked,
    KillSwitchExecutor,
    StrategyScheduler,
    evaluate_strategy,
    idempotency_key,
    position_state,
)
from ezchain_engine.engine import should_run


class FakeSource:
    def __init__(
        self,
        snapshot: TokenSnapshot | None = None,
        error: Exception | None = None,
    ) -> None:
        self.snapshot = snapshot or make_snapshot()
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def get_token(self, chain: str, address: str) -> TokenSnapshot:
        self.calls.append((chain, address))
        if self.error is not None:
            raise self.error
        return self.snapshot


class FakeExecutor:
    def __init__(self, order_id: str = "order-1", error: Exception | None = None) -> None:
        self.order_id = order_id
        self.error = error
        self.requests: list[OrderRequest] = []

    async def place_order(self, request: OrderRequest) -> str:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.order_id


async def run_once(
    session_factory: sessionmaker[Session],
    strategy_id: str,
    source: FakeSource,
    executor: Any,
    *,
    now: datetime | None = None,
) -> Any:
    with session_factory() as session:
        strategy = session.get(StrategyRow, strategy_id)
        assert strategy is not None
        return await evaluate_strategy(session, strategy, source, executor, now=now)


def get_strategy(session_factory: sessionmaker[Session], strategy_id: str) -> StrategyRow:
    with session_factory() as session:
        row = session.get(StrategyRow, strategy_id)
        assert row is not None
        return row


def runs_for(session_factory: sessionmaker[Session], strategy_id: str) -> list[StrategyRunRow]:
    with session_factory() as session:
        return list(
            session.query(StrategyRunRow)
            .filter(StrategyRunRow.strategy_id == strategy_id)
            .order_by(StrategyRunRow.created_at)
        )


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


async def test_buy_places_order_and_writes_the_run_log(
    session_factory: sessionmaker[Session], add_strategy: Any
) -> None:
    strategy_id = add_strategy()
    source = FakeSource(make_snapshot(change_h1="6"))
    executor = FakeExecutor(order_id="ord-42")
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)

    result = await run_once(session_factory, strategy_id, source, executor, now=now)

    assert result.action == "buy"
    assert result.reason == "MOMENTUM_ENTRY"
    assert result.amount_usd == Decimal("100")
    assert result.order_id == "ord-42"
    assert result.run_id is not None
    assert result.inputs["change_pct"] == "6"

    assert len(executor.requests) == 1
    request = executor.requests[0]
    assert request.strategy_id == strategy_id
    assert request.side == "buy"
    assert request.amount_usd == Decimal("100")
    assert request.chain == CHAIN
    assert request.token_address == TOKEN

    rows = runs_for(session_factory, strategy_id)
    assert len(rows) == 1
    assert rows[0].id == result.run_id
    assert rows[0].action == "buy"
    assert rows[0].reason == "MOMENTUM_ENTRY"
    assert rows[0].order_id == "ord-42"
    assert as_utc(get_strategy(session_factory, strategy_id).last_run_at) == now


async def test_one_decision_per_interval(
    session_factory: sessionmaker[Session], add_strategy: Any
) -> None:
    strategy_id = add_strategy(params={"interval_minutes": 60})
    source = FakeSource()
    executor = FakeExecutor()
    first = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)

    await run_once(session_factory, strategy_id, source, executor, now=first)
    blocked = await run_once(
        session_factory,
        strategy_id,
        source,
        executor,
        now=first + timedelta(minutes=5),
    )

    assert blocked.action == "hold"
    assert blocked.reason == "INTERVAL_NOT_ELAPSED"
    assert len(executor.requests) == 1
    assert as_utc(get_strategy(session_factory, strategy_id).last_run_at) == first

    # The boundary itself opens the gate again.
    allowed = await run_once(
        session_factory,
        strategy_id,
        source,
        executor,
        now=first + timedelta(minutes=60),
    )
    assert allowed.action == "buy"
    assert len(executor.requests) == 2
    assert len(runs_for(session_factory, strategy_id)) == 3


async def test_paused_strategy_skips_fetch_and_execution(
    session_factory: sessionmaker[Session], add_strategy: Any
) -> None:
    strategy_id = add_strategy(status="paused")
    source = FakeSource()
    executor = FakeExecutor()

    result = await run_once(session_factory, strategy_id, source, executor)

    assert result.action == "hold"
    assert result.reason == "PAUSED"
    assert result.run_id is None
    assert source.calls == []
    assert executor.requests == []
    assert runs_for(session_factory, strategy_id) == []
    assert get_strategy(session_factory, strategy_id).last_run_at is None


async def test_dca_interval_is_gated_by_the_scheduler(
    session_factory: sessionmaker[Session], add_strategy: Any
) -> None:
    start = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    strategy_id = add_strategy(
        kind="dca",
        params={"order_usd": 50, "interval_minutes": 60, "max_position_usd": 500},
        last_run_at=start,
    )
    source = FakeSource()
    executor = FakeExecutor()

    blocked = await run_once(
        session_factory, strategy_id, source, executor, now=start + timedelta(minutes=30)
    )
    assert blocked.reason == "INTERVAL_NOT_ELAPSED"
    assert executor.requests == []

    due = await run_once(
        session_factory, strategy_id, source, executor, now=start + timedelta(minutes=60)
    )
    assert due.action == "buy"
    assert due.amount_usd == Decimal("50")


async def test_dca_stops_at_the_position_limit(
    session_factory: sessionmaker[Session], add_strategy: Any
) -> None:
    strategy_id = add_strategy(
        kind="dca",
        params={"order_usd": 100, "max_position_usd": 500, "interval_minutes": 1},
    )
    with session_factory() as session:
        session.add(
            LedgerEntryRow(
                group_id="g1",
                account=position_account(CHAIN, TOKEN),
                asset=TOKEN,
                amount=Decimal("480"),
                usd_value=Decimal("480"),
                kind="buy",
            )
        )
        session.commit()
    source = FakeSource()
    executor = FakeExecutor()

    result = await run_once(session_factory, strategy_id, source, executor)

    assert result.action == "buy"
    assert result.amount_usd == Decimal("20")
    assert executor.requests[0].amount_usd == Decimal("20")


async def test_take_profit_sells_the_ledger_position(
    session_factory: sessionmaker[Session], add_strategy: Any
) -> None:
    strategy_id = add_strategy(
        params={"take_profit_pct": 20, "stop_loss_pct": 8, "interval_minutes": 60}
    )
    with session_factory() as session:
        session.add(
            LedgerEntryRow(
                group_id="g1",
                account=position_account(CHAIN, TOKEN),
                asset=TOKEN,
                amount=Decimal("100"),
                usd_value=Decimal("100"),
                kind="buy",
            )
        )
        session.commit()
    source = FakeSource(make_snapshot(price="1.25", change_h1="1"))
    executor = FakeExecutor(order_id="sell-1")

    result = await run_once(session_factory, strategy_id, source, executor)

    assert result.action == "sell"
    assert result.reason == "TAKE_PROFIT"
    assert executor.requests[0].side == "sell"
    assert executor.requests[0].amount_usd == Decimal("125.00")
    assert runs_for(session_factory, strategy_id)[0].order_id == "sell-1"


async def test_market_data_failure_fails_closed_without_consuming_the_window(
    session_factory: sessionmaker[Session], add_strategy: Any
) -> None:
    strategy_id = add_strategy()
    source = FakeSource(error=RuntimeError("429"))
    executor = FakeExecutor()

    result = await run_once(session_factory, strategy_id, source, executor)

    assert result.action == "hold"
    assert result.reason == "MARKET_DATA_UNAVAILABLE"
    assert executor.requests == []
    assert result.run_id is not None
    assert get_strategy(session_factory, strategy_id).last_run_at is None


async def test_executor_failure_is_logged_and_retried(
    session_factory: sessionmaker[Session], add_strategy: Any
) -> None:
    strategy_id = add_strategy()
    source = FakeSource()
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    failing = FakeExecutor(error=RuntimeError("venue down"))

    failed = await run_once(session_factory, strategy_id, source, failing, now=now)

    assert failed.action == "hold"
    assert failed.reason == "EXECUTION_FAILED"
    assert failed.run_id is not None
    assert get_strategy(session_factory, strategy_id).last_run_at is None

    healthy = FakeExecutor(order_id="ord-retry")
    retried = await run_once(session_factory, strategy_id, source, healthy, now=now)
    assert retried.action == "buy"
    assert retried.order_id == "ord-retry"


async def test_missing_executor_is_explicit(
    session_factory: sessionmaker[Session], add_strategy: Any
) -> None:
    strategy_id = add_strategy()
    result = await run_once(session_factory, strategy_id, FakeSource(), MissingOrderExecutor())
    assert result.reason == "ORDER_EXECUTOR_UNAVAILABLE"
    assert result.order_id is None
    assert get_strategy(session_factory, strategy_id).last_run_at is None


async def test_kill_switch_blocks_strategy_order_and_logs_a_hold(
    session_factory: sessionmaker[Session], add_strategy: Any
) -> None:
    strategy_id = add_strategy()
    inner = FakeExecutor()
    guard = KillSwitchExecutor(inner, lambda: True)
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)

    result = await run_once(session_factory, strategy_id, FakeSource(), guard, now=now)

    assert result.action == "hold"
    assert result.reason == "KILL_SWITCH_ACTIVE"
    assert result.order_id is None
    assert result.run_id is not None
    assert result.inputs["attempted_action"] == "buy"
    assert Decimal(str(result.inputs["attempted_amount_usd"])) == Decimal("100")
    assert inner.requests == []
    # The window is not consumed: releasing the switch retries immediately.
    assert get_strategy(session_factory, strategy_id).last_run_at is None
    rows = runs_for(session_factory, strategy_id)
    assert len(rows) == 1
    assert rows[0].action == "hold"
    assert rows[0].reason == "KILL_SWITCH_ACTIVE"


async def test_releasing_the_kill_switch_restores_execution(
    session_factory: sessionmaker[Session], add_strategy: Any
) -> None:
    strategy_id = add_strategy()
    inner = FakeExecutor(order_id="ord-after-release")
    active = {"on": True}
    guard = KillSwitchExecutor(inner, lambda: active["on"])
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)

    blocked = await run_once(session_factory, strategy_id, FakeSource(), guard, now=now)
    assert blocked.reason == "KILL_SWITCH_ACTIVE"

    active["on"] = False
    allowed = await run_once(
        session_factory, strategy_id, FakeSource(), guard, now=now + timedelta(minutes=1)
    )
    assert allowed.action == "buy"
    assert allowed.order_id == "ord-after-release"
    assert len(inner.requests) == 1
    assert as_utc(get_strategy(session_factory, strategy_id).last_run_at) == now + timedelta(
        minutes=1
    )


async def test_kill_switch_allows_manual_orders(
    session_factory: sessionmaker[Session],
) -> None:
    inner = FakeExecutor(order_id="manual-order")
    guard = KillSwitchExecutor(inner, lambda: True)
    manual = OrderRequest(
        chain=CHAIN,
        token_address=TOKEN,
        side="buy",
        amount_usd=Decimal("10"),
        idempotency_key="manual-key-1",
    )

    assert await guard.place_order(manual) == "manual-order"
    assert inner.requests == [manual]


async def test_kill_switch_raises_execution_blocked_for_strategy_orders(
    session_factory: sessionmaker[Session],
) -> None:
    guard = KillSwitchExecutor(FakeExecutor(), lambda: True)
    strategy_order = OrderRequest(
        chain=CHAIN,
        token_address=TOKEN,
        side="buy",
        amount_usd=Decimal("10"),
        idempotency_key="strategy-key-1",
        strategy_id="strategy-1",
    )

    with pytest.raises(ExecutionBlocked) as raised:
        await guard.place_order(strategy_order)
    assert raised.value.code == "KILL_SWITCH_ACTIVE"


async def test_scheduler_skips_the_pass_without_touching_market_data_while_armed(
    session_factory: sessionmaker[Session], add_strategy: Any
) -> None:
    strategy_id = add_strategy(params={"interval_minutes": 60})
    source = FakeSource()
    inner = FakeExecutor()
    guard = KillSwitchExecutor(inner, lambda: True)
    scheduler = StrategyScheduler(session_factory, source, guard, interval_seconds=1)

    assert guard.active is True
    assert await scheduler.run_once() == []

    assert source.calls == []
    assert inner.requests == []
    assert runs_for(session_factory, strategy_id) == []
    assert get_strategy(session_factory, strategy_id).last_run_at is None


async def test_run_log_insert_after_strategy_delete_is_dropped(
    session_factory: sessionmaker[Session], add_strategy: Any
) -> None:
    strategy_id = add_strategy()
    # Load the row, then let another actor delete it before the run is recorded.
    with session_factory() as loaded:
        strategy = loaded.get(StrategyRow, strategy_id)
        assert strategy is not None
    with session_factory() as deleter:
        deleter.execute(delete(StrategyRunRow).where(StrategyRunRow.strategy_id == strategy_id))
        row = deleter.get(StrategyRow, strategy_id)
        assert row is not None
        deleter.delete(row)
        deleter.commit()

    with session_factory() as session:
        result = await evaluate_strategy(
            session,
            strategy,
            FakeSource(),
            FakeExecutor(order_id="order-orphaned"),
            now=datetime(2026, 10, 22, 12, 0, tzinfo=UTC),
        )

    assert result.action == "buy"
    assert result.run_id is None
    with session_factory() as session:
        assert session.get(StrategyRow, strategy_id) is None


async def test_invalid_persisted_params_are_reported(
    session_factory: sessionmaker[Session], add_strategy: Any
) -> None:
    strategy_id = add_strategy(params={"lookback": "1d"})
    executor = FakeExecutor()

    result = await run_once(session_factory, strategy_id, FakeSource(), executor)

    assert result.reason == "INVALID_PARAMS"
    assert result.inputs["error"] == "STRATEGY_LOOKBACK_UNSUPPORTED"
    assert executor.requests == []


async def test_scheduler_run_once_covers_active_strategies_only(
    session_factory: sessionmaker[Session], add_strategy: Any
) -> None:
    active_a = add_strategy(params={"interval_minutes": 60})
    active_b = add_strategy(kind="dca", params={"interval_minutes": 60})
    add_strategy(status="paused")
    source = FakeSource(make_snapshot(change_h1="6"))
    executor = FakeExecutor()
    scheduler = StrategyScheduler(session_factory, source, executor, interval_seconds=1)

    results = await scheduler.run_once()

    assert {result.strategy_id for result in results} == {active_a, active_b}
    assert len(executor.requests) == 2
    assert len(source.calls) == 2


async def test_scheduler_skips_not_due_strategies_without_a_run_row(
    session_factory: sessionmaker[Session], add_strategy: Any
) -> None:
    start = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    strategy_id = add_strategy(
        params={"interval_minutes": 60}, last_run_at=start - timedelta(minutes=1)
    )
    scheduler = StrategyScheduler(
        session_factory,
        FakeSource(),
        FakeExecutor(),
        interval_seconds=1,
        now=lambda: start,
    )

    assert await scheduler.run_once() == []
    assert runs_for(session_factory, strategy_id) == []


async def test_scheduler_run_once_with_no_active_strategies(
    session_factory: sessionmaker[Session], add_strategy: Any
) -> None:
    add_strategy(status="paused")
    scheduler = StrategyScheduler(session_factory, FakeSource(), FakeExecutor(), interval_seconds=1)
    assert await scheduler.run_once() == []


def test_should_run_normalizes_naive_and_aware_timestamps() -> None:
    naive = datetime(2026, 10, 22, 12, 0)
    aware = datetime(2026, 10, 22, 13, 0, tzinfo=UTC)
    assert should_run(naive, aware, 60)
    assert not should_run(naive, aware, 61)
    assert not should_run(aware, datetime(2026, 10, 22, 12, 0, tzinfo=UTC), 60)


def test_position_state_replays_ledger_entries(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        session.add_all(
            [
                LedgerEntryRow(
                    group_id="g1",
                    account=position_account(CHAIN, TOKEN),
                    asset=TOKEN,
                    amount=Decimal("100"),
                    usd_value=Decimal("100"),
                    kind="buy",
                ),
                LedgerEntryRow(
                    group_id="g2",
                    account=position_account(CHAIN, TOKEN),
                    asset=TOKEN,
                    amount=Decimal("-40"),
                    usd_value=Decimal("-40"),
                    kind="sell",
                ),
            ]
        )
        session.commit()
        position = position_state(session, CHAIN, TOKEN)
    assert position.quantity == Decimal("60")
    assert position.cost_usd == Decimal("60")
    assert position.avg_cost_usd == Decimal("1")


def test_idempotency_key_is_stable_within_a_window() -> None:
    start = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    later = start + timedelta(minutes=59)
    after = start + timedelta(hours=1)
    assert idempotency_key("s1", start, 60) == idempotency_key("s1", later, 60)
    assert idempotency_key("s1", start, 60) != idempotency_key("s1", after, 60)
