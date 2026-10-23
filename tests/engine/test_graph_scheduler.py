"""Graph strategies run through the same scheduler, run log and order path."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from helpers import CHAIN, TOKEN, make_snapshot
from sqlalchemy.orm import Session, sessionmaker

from ezchain_api.models import StrategyRow, StrategyRunRow
from ezchain_core.graph import EvalContext
from ezchain_core.types import OrderRequest, TokenSnapshot
from ezchain_engine import StrategyScheduler, evaluate_strategy, latest_graph_vars

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def node(node_id: str, node_type: str, **params: Any) -> dict[str, Any]:
    return {"id": node_id, "type": node_type, "params": params, "ui": {}}


def edge(source: str, source_port: str, target: str, target_port: str) -> dict[str, Any]:
    return {
        "from": {"node": source, "port": source_port},
        "to": {"node": target, "port": target_port},
    }


def price_gate_doc(*, sell_pct: str = "100") -> dict[str, Any]:
    return {
        "schema": "ezchain.graph/1",
        "nodes": [
            node("tick", "event.tick"),
            node("seq", "flow.sequence"),
            node("price", "data.price"),
            node("pos", "pos.state"),
            node("below", "math.const", value="2"),
            node("above", "math.const", value="1.5"),
            node("cheap", "math.compare", op="<"),
            node("rich", "math.compare", op=">"),
            node("flat", "logic.not"),
            node("buy_ok", "logic.and"),
            node("sell_ok", "logic.and"),
            node("buy_branch", "flow.branch"),
            node("sell_branch", "flow.branch"),
            node("buy", "act.buy", amount_usd="100"),
            node("sell", "act.sell", pct_position=sell_pct),
        ],
        "edges": [
            edge("tick", "out", "seq", "in"),
            edge("seq", "then0", "buy_branch", "in"),
            edge("seq", "then1", "sell_branch", "in"),
            edge("price", "value", "cheap", "a"),
            edge("below", "value", "cheap", "b"),
            edge("price", "value", "rich", "a"),
            edge("above", "value", "rich", "b"),
            edge("pos", "open", "flat", "a"),
            edge("cheap", "out", "buy_ok", "a"),
            edge("flat", "out", "buy_ok", "b"),
            edge("rich", "out", "sell_ok", "a"),
            edge("pos", "open", "sell_ok", "b"),
            edge("buy_ok", "out", "buy_branch", "cond"),
            edge("sell_ok", "out", "sell_branch", "cond"),
            edge("buy_branch", "true", "buy", "in"),
            edge("sell_branch", "true", "sell", "in"),
        ],
        "comments": [],
    }


def set_var_doc() -> dict[str, Any]:
    return {
        "schema": "ezchain.graph/1",
        "nodes": [
            node("tick", "event.tick"),
            node("five", "math.const", value="5"),
            node("var", "act.set_var", name="entry"),
            node("buy", "act.buy", amount_usd="10"),
        ],
        "edges": [
            edge("tick", "out", "var", "in"),
            edge("var", "out", "buy", "in"),
            edge("five", "value", "var", "value"),
        ],
        "comments": [],
    }


class FakeSource:
    def __init__(self, snapshot: TokenSnapshot | None = None) -> None:
        self.snapshot = snapshot or make_snapshot()

    async def get_token(self, chain: str, address: str) -> TokenSnapshot:
        return self.snapshot


class FakeExecutor:
    def __init__(self, order_id: str = "ord-1") -> None:
        self.order_id = order_id
        self.requests: list[OrderRequest] = []

    async def place_order(self, request: OrderRequest) -> str:
        self.requests.append(request)
        return self.order_id


class FakeGraphRuntime:
    """Stand-in for the API composition: returns a prepared, frozen context."""

    def __init__(self, context: EvalContext) -> None:
        self.context = context
        self.calls = 0

    async def build_context(
        self,
        session: Session,
        strategy: StrategyRow,
        *,
        snapshot: TokenSnapshot,
        now: datetime,
    ) -> EvalContext:
        self.calls += 1
        return self.context


def context(
    snapshot: TokenSnapshot,
    *,
    quantity: str = "0",
    average: str | None = None,
    value: str | None = None,
    cash: str = "1000",
    now: datetime = NOW,
) -> EvalContext:
    return EvalContext(
        now=now,
        snapshot=snapshot,
        position_quantity=Decimal(quantity),
        position_avg_cost_usd=None if average is None else Decimal(average),
        position_value_usd=None if value is None else Decimal(value),
        cash_usd=Decimal(cash),
        equity_usd=Decimal(cash) + (Decimal(0) if value is None else Decimal(value)),
        max_position_usd=Decimal("500"),
    )


def add_graph_strategy(
    session_factory: sessionmaker[Session],
    *,
    document: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    last_run_at: datetime | None = None,
) -> str:
    with session_factory() as session:
        row = StrategyRow(
            name="graph test",
            chain=CHAIN,
            token_address=TOKEN,
            token_symbol="TEST",
            kind="graph",
            params=params
            or {
                "graph": document or price_gate_doc(),
                "interval_minutes": 1,
                "max_position_usd": "500",
            },
            status="active",
            last_run_at=last_run_at,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id


def runs(session_factory: sessionmaker[Session], strategy_id: str) -> list[StrategyRunRow]:
    with session_factory() as session:
        return list(
            session.query(StrategyRunRow)
            .filter(StrategyRunRow.strategy_id == strategy_id)
            .order_by(StrategyRunRow.created_at)
        )


async def test_graph_buy_uses_amount_usd_and_logs_the_trace(
    session_factory: sessionmaker[Session],
) -> None:
    strategy_id = add_graph_strategy(session_factory)
    source = FakeSource(make_snapshot(price="1"))
    executor = FakeExecutor("graph-buy")
    runtime = FakeGraphRuntime(context(make_snapshot(price="1")))

    with session_factory() as session:
        strategy = session.get(StrategyRow, strategy_id)
        assert strategy is not None
        result = await evaluate_strategy(
            session, strategy, source, executor, graph_runtime=runtime, now=NOW
        )

    assert result.action == "buy"
    assert result.reason == "GRAPH_BUY"
    assert result.amount_usd == Decimal("100")
    assert result.order_id == "graph-buy"
    assert executor.requests[0].amount_usd == Decimal("100")
    assert executor.requests[0].strategy_id == strategy_id
    assert result.inputs["graph_trace"]["exec"][0] == "tick"
    assert result.inputs["context_digest"]
    rows = runs(session_factory, strategy_id)
    assert rows[0].action == "buy"


async def test_graph_sell_uses_fraction_of_position_value(
    session_factory: sessionmaker[Session],
) -> None:
    strategy_id = add_graph_strategy(session_factory, document=price_gate_doc(sell_pct="50"))
    snapshot = make_snapshot(price="2")
    executor = FakeExecutor("graph-sell")
    runtime = FakeGraphRuntime(context(snapshot, quantity="10", average="1", value="20"))

    with session_factory() as session:
        strategy = session.get(StrategyRow, strategy_id)
        assert strategy is not None
        result = await evaluate_strategy(
            session, strategy, FakeSource(snapshot), executor, graph_runtime=runtime, now=NOW
        )

    assert result.action == "sell"
    assert result.reason == "GRAPH_SELL"
    # 50% of a $20 position.
    assert executor.requests[0].amount_usd == Decimal("10")
    assert executor.requests[0].side == "sell"


async def test_graph_full_exit_never_oversells(
    session_factory: sessionmaker[Session],
) -> None:
    strategy_id = add_graph_strategy(session_factory)
    snapshot = make_snapshot(price="2")
    executor = FakeExecutor("graph-exit")
    runtime = FakeGraphRuntime(context(snapshot, quantity="10", average="1", value="20"))

    with session_factory() as session:
        strategy = session.get(StrategyRow, strategy_id)
        assert strategy is not None
        result = await evaluate_strategy(
            session, strategy, FakeSource(snapshot), executor, graph_runtime=runtime, now=NOW
        )

    assert result.action == "sell"
    assert executor.requests[0].amount_usd == Decimal("20")


async def test_graph_hold_logs_honestly_and_places_no_order(
    session_factory: sessionmaker[Session],
) -> None:
    strategy_id = add_graph_strategy(session_factory)
    snapshot = make_snapshot(price=None)
    executor = FakeExecutor()
    runtime = FakeGraphRuntime(context(snapshot))

    with session_factory() as session:
        strategy = session.get(StrategyRow, strategy_id)
        assert strategy is not None
        result = await evaluate_strategy(
            session, strategy, FakeSource(snapshot), executor, graph_runtime=runtime, now=NOW
        )

    assert result.action == "hold"
    assert result.reason == "UNKNOWN_CONDITION"
    assert executor.requests == []
    assert runs(session_factory, strategy_id)[0].action == "hold"


async def test_missing_graph_runtime_fails_closed(
    session_factory: sessionmaker[Session],
) -> None:
    strategy_id = add_graph_strategy(session_factory)
    executor = FakeExecutor()

    with session_factory() as session:
        strategy = session.get(StrategyRow, strategy_id)
        assert strategy is not None
        result = await evaluate_strategy(session, strategy, FakeSource(), executor, now=NOW)

    assert result.reason == "GRAPH_RUNTIME_UNAVAILABLE"
    assert executor.requests == []


async def test_set_var_effect_is_written_to_the_run_log(
    session_factory: sessionmaker[Session],
) -> None:
    strategy_id = add_graph_strategy(session_factory, document=set_var_doc())
    snapshot = make_snapshot(price="1")
    executor = FakeExecutor()
    runtime = FakeGraphRuntime(context(snapshot))

    with session_factory() as session:
        strategy = session.get(StrategyRow, strategy_id)
        assert strategy is not None
        await evaluate_strategy(
            session, strategy, FakeSource(snapshot), executor, graph_runtime=runtime, now=NOW
        )

    row = runs(session_factory, strategy_id)[0]
    assert row.inputs["graph_vars"] == {"entry": "5"}
    effects = row.inputs["effects"]
    assert any(effect["kind"] == "set_var" and effect["name"] == "entry" for effect in effects)


async def test_interval_hold_preserves_the_last_graph_vars(
    session_factory: sessionmaker[Session],
) -> None:
    strategy_id = add_graph_strategy(session_factory, document=set_var_doc())
    snapshot = make_snapshot(price="1")
    runtime = FakeGraphRuntime(context(snapshot))

    with session_factory() as session:
        strategy = session.get(StrategyRow, strategy_id)
        assert strategy is not None
        await evaluate_strategy(
            session, strategy, FakeSource(snapshot), FakeExecutor(), graph_runtime=runtime, now=NOW
        )

    with session_factory() as session:
        strategy = session.get(StrategyRow, strategy_id)
        assert strategy is not None
        held = await evaluate_strategy(
            session,
            strategy,
            FakeSource(snapshot),
            FakeExecutor(),
            graph_runtime=runtime,
            now=NOW + timedelta(seconds=30),
        )

    assert held.reason == "INTERVAL_NOT_ELAPSED"
    assert held.inputs["graph_vars"] == {"entry": "5"}
    # The value is read back and would be available to the next due tick.
    with session_factory() as session:
        assert latest_graph_vars(session, strategy_id) == {"entry": Decimal("5")}


async def test_scheduler_runs_a_graph_strategy(
    session_factory: sessionmaker[Session],
) -> None:
    strategy_id = add_graph_strategy(session_factory)
    executor = FakeExecutor("scheduled-buy")
    runtime = FakeGraphRuntime(context(make_snapshot(price="1")))
    scheduler = StrategyScheduler(
        session_factory,
        FakeSource(make_snapshot(price="1")),
        executor,
        graph_runtime=runtime,
        interval_seconds=1,
        now=lambda: NOW,
    )

    results = await scheduler.run_once()

    assert [result.strategy_id for result in results] == [strategy_id]
    assert results[0].action == "buy"
    assert executor.requests[0].amount_usd == Decimal("100")
    assert runtime.calls == 1


async def test_graph_context_failure_does_not_consume_the_window(
    session_factory: sessionmaker[Session],
) -> None:
    strategy_id = add_graph_strategy(session_factory)

    class BrokenRuntime:
        async def build_context(
            self,
            session: Session,
            strategy: StrategyRow,
            *,
            snapshot: TokenSnapshot,
            now: datetime,
        ) -> EvalContext:
            raise RuntimeError("candles unavailable")

    with session_factory() as session:
        strategy = session.get(StrategyRow, strategy_id)
        assert strategy is not None
        result = await evaluate_strategy(
            session,
            strategy,
            FakeSource(),
            FakeExecutor(),
            graph_runtime=BrokenRuntime(),
            now=NOW,
        )

    assert result.reason == "GRAPH_CONTEXT_UNAVAILABLE"
    with session_factory() as session:
        assert session.get(StrategyRow, strategy_id).last_run_at is None  # type: ignore[union-attr]
    assert runs(session_factory, strategy_id)[0].action == "hold"


async def test_interval_gate_still_applies_to_graph(
    session_factory: sessionmaker[Session],
) -> None:
    # The scheduler's due check uses the graph's own interval, like every other kind.
    strategy_id = add_graph_strategy(
        session_factory,
        params={
            "graph": price_gate_doc(),
            "interval_minutes": 60,
            "max_position_usd": "500",
        },
        last_run_at=NOW - timedelta(minutes=30),
    )
    scheduler = StrategyScheduler(
        session_factory,
        FakeSource(),
        FakeExecutor(),
        graph_runtime=FakeGraphRuntime(context(make_snapshot(price="1"))),
        interval_seconds=1,
        now=lambda: NOW,
    )

    assert await scheduler.run_once() == []
    assert runs(session_factory, strategy_id) == []
