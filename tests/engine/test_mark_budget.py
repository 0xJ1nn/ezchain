"""The engine's mark budget makes ``TokenSnapshot.stale`` a truth, not a constant.

Every engine-side market read is a mark read, so it carries the same budget the API
passes. Without it the adapter cannot tell a fresh mark from an old one, ``stale`` stays
``False`` on every snapshot, and the graph/plugin guards that consult it never fire.
These probes age the source past the budget and assert the engine flags it and the two
paths refuse to price from it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from helpers import CHAIN, TOKEN, make_snapshot
from sqlalchemy.orm import Session, sessionmaker

from ezchain_api.models import LedgerEntryRow, StrategyRow
from ezchain_core.graph import EvalContext
from ezchain_core.ledger import position_account
from ezchain_core.types import OrderRequest, TokenSnapshot
from ezchain_engine import StrategyScheduler, evaluate_strategy
from ezchain_engine.plugin_runtime import evaluate_plugin_strategy

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
BUDGET = 60.0


class BudgetedSource:
    """Applies the budget it is given, the way the real adapter does.

    It records the budget per call so a test can prove the engine passed one, and it
    derives ``stale`` from the observation age exactly as ``snapshot_from_pairs`` does.
    """

    def __init__(self, *, age_seconds: float) -> None:
        self.observed_at = NOW - timedelta(seconds=age_seconds)
        self.budgets: list[float | None] = []

    async def get_token(
        self, chain: str, address: str, *, max_age_seconds: float | None = None
    ) -> TokenSnapshot:
        self.budgets.append(max_age_seconds)
        age = (NOW - self.observed_at).total_seconds()
        snapshot = make_snapshot(observed_at=self.observed_at)
        return TokenSnapshot(
            **{
                **snapshot.__dict__,
                "stale": False if max_age_seconds is None else age > max_age_seconds,
            }
        )


class FakeExecutor:
    def __init__(self) -> None:
        self.requests: list[OrderRequest] = []

    async def place_order(self, request: OrderRequest) -> str:
        self.requests.append(request)
        return "ord-budget-1"


class FakeGraphRuntime:
    def __init__(self, context: EvalContext) -> None:
        self.context = context

    async def build_context(
        self,
        session: Session,
        strategy: StrategyRow,
        *,
        snapshot: TokenSnapshot,
        now: datetime,
    ) -> EvalContext:
        return self.context


def add_strategy(session_factory: sessionmaker[Session], *, kind: str = "momentum") -> str:
    params: dict[str, object]
    if kind == "graph":
        params = {
            "graph": {
                "schema": "ezchain.graph/1",
                "nodes": [
                    {"id": "tick", "type": "event.tick", "params": {}, "ui": {}},
                    {"id": "sell", "type": "act.sell", "params": {"pct_position": "50"}, "ui": {}},
                ],
                "edges": [],
                "comments": [],
            },
            "interval_minutes": 1,
            "max_position_usd": "500",
        }
    else:
        params = {"interval_minutes": 1}
    with session_factory() as session:
        row = StrategyRow(
            name="budget",
            chain=CHAIN,
            token_address=TOKEN,
            token_symbol="TEST",
            kind=kind,
            params=params,
            status="active",
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id


def add_position(session_factory: sessionmaker[Session], *, quantity: str = "10") -> None:
    with session_factory() as session:
        session.add(
            LedgerEntryRow(
                group_id="g-budget",
                account=position_account(CHAIN, TOKEN),
                asset=TOKEN,
                amount=Decimal(quantity),
                usd_value=Decimal("10"),
                kind="buy",
            )
        )
        session.commit()


def context(snapshot: TokenSnapshot, *, quantity: str = "10") -> EvalContext:
    """A context with no position value, so an exit has to price from the snapshot."""
    return EvalContext(
        now=NOW,
        snapshot=snapshot,
        position_quantity=Decimal(quantity),
        position_avg_cost_usd=Decimal("1"),
        position_value_usd=None,
        cash_usd=Decimal("1000"),
        equity_usd=Decimal("1000"),
        max_position_usd=Decimal("500"),
    )


async def test_the_engine_passes_the_mark_budget_to_the_scheduler_read(
    session_factory: sessionmaker[Session],
) -> None:
    add_strategy(session_factory)
    source = BudgetedSource(age_seconds=5)
    scheduler = StrategyScheduler(
        session_factory,
        source,  # type: ignore[arg-type]
        FakeExecutor(),
        max_mark_age_seconds=BUDGET,
        now=lambda: NOW,
    )

    await scheduler.run_once()

    assert source.budgets, "the engine never asked for a budgeted mark"
    assert set(source.budgets) == {BUDGET}


async def test_the_engine_passes_the_mark_budget_to_the_strategy_read(
    session_factory: sessionmaker[Session],
) -> None:
    strategy_id = add_strategy(session_factory)
    source = BudgetedSource(age_seconds=5)

    with session_factory() as session:
        strategy = session.get(StrategyRow, strategy_id)
        assert strategy is not None
        await evaluate_strategy(
            session,
            strategy,
            source,  # type: ignore[arg-type]
            FakeExecutor(),
            max_mark_age_seconds=BUDGET,
            now=NOW,
        )

    assert source.budgets == [BUDGET]


async def test_the_protection_pass_carries_the_mark_budget(
    session_factory: sessionmaker[Session],
) -> None:
    from ezchain_api.models import ProtectionArmRow
    from ezchain_engine.protection import evaluate_all_arms

    add_position(session_factory)
    with session_factory() as session:
        session.add(
            ProtectionArmRow(
                id="arm-budget",
                chain=CHAIN,
                token_address=TOKEN,
                token_symbol="TEST",
                kind="stop_loss",
                status="armed",
                params={"price_usd": "0.10"},
                peak_usd=None,
                triggered_levels=[],
                profile_id=None,
            )
        )
        session.commit()
    source = BudgetedSource(age_seconds=5)

    await evaluate_all_arms(
        session_factory,
        source,  # type: ignore[arg-type]
        FakeExecutor(),
        max_mark_age_seconds=BUDGET,
        now=NOW,
    )

    assert source.budgets == [BUDGET]


async def test_an_engine_side_stale_mark_is_flagged_and_refuses_the_plan(
    session_factory: sessionmaker[Session],
) -> None:
    strategy_id = add_strategy(session_factory)
    source = BudgetedSource(age_seconds=2 * 3600)

    snapshot = await source.get_token(CHAIN, TOKEN, max_age_seconds=BUDGET)
    assert snapshot.stale is True

    with session_factory() as session:
        strategy = session.get(StrategyRow, strategy_id)
        assert strategy is not None
        result = await evaluate_strategy(
            session,
            strategy,
            source,  # type: ignore[arg-type]
            FakeExecutor(),
            max_mark_age_seconds=BUDGET,
            now=NOW,
        )

    assert result.action == "hold"
    assert result.reason == "MARK_STALE"


async def test_the_graph_path_will_not_size_an_exit_from_a_stale_mark(
    session_factory: sessionmaker[Session],
) -> None:
    add_position(session_factory)
    strategy_id = add_strategy(session_factory, kind="graph")
    stale = make_snapshot(observed_at=NOW - timedelta(hours=2), stale=True)
    executor = FakeExecutor()

    with session_factory() as session:
        strategy = session.get(StrategyRow, strategy_id)
        assert strategy is not None
        result = await evaluate_strategy(
            session,
            strategy,
            BudgetedSource(age_seconds=2 * 3600),  # type: ignore[arg-type]
            executor,
            graph_runtime=FakeGraphRuntime(context(stale)),  # type: ignore[arg-type]
            max_mark_age_seconds=BUDGET,
            now=NOW,
        )

    # No position value and a stale mark: the graph cannot compute a sell amount, so no
    # order is placed instead of pricing the exit from the dead number.
    assert result.action == "hold"
    assert executor.requests == []


async def test_the_plugin_path_will_not_value_a_position_from_a_stale_mark(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    """A stale mark reaches the plugin host as no price, so a position is unpriced."""
    import json
    import textwrap

    from ezchain_engine import PluginHost, PluginRegistry

    root = tmp_path / "budget-plugin"
    root.mkdir()
    (root / "ezchain.plugin.json").write_text(
        json.dumps(
            {
                "id": "com.example.budget",
                "name": "Budget plugin",
                "version": "1.0.0",
                "sdk": ">=1,<2",
                "entrypoint": "budget_plugin:Plugin",
                "permissions": ["read_market", "submit_intent"],
                "params_schema": {
                    "type": "object",
                    "properties": {"entry_pct": {"type": "number", "default": 1}},
                    "additionalProperties": False,
                },
                "timeframes": ["1h"],
                "primary_timeframe": "1h",
            }
        )
    )
    (root / "budget_plugin.py").write_text(
        textwrap.dedent(
            """
            from decimal import Decimal
            from ezchain_sdk import Intent, Strategy

            class Plugin(Strategy):
                def on_candle(self, ctx, candle):
                    return [Intent(side="buy", notional_usd=Decimal("100"), reason="BIG")]
            """
        )
    )
    registry = PluginRegistry(paths=[tmp_path], include_entry_points=False)

    class Runtime:
        async def build_context(self, session, strategy, *, snapshot, now):  # type: ignore[no-untyped-def]
            from ezchain_sdk import Candle, PluginContext

            bar = Candle(
                open_time=now,
                open=Decimal("1"),
                high=Decimal("1"),
                low=Decimal("1"),
                close=Decimal("1"),
            )
            return PluginContext(
                now=now,
                mode="paper",
                params={"entry_pct": 1},
                state={},
                candles={"1h": (bar,)},
            )

    add_position(session_factory, quantity="10")
    stale = make_snapshot(observed_at=NOW - timedelta(hours=2), stale=True)
    host = PluginHost(timeout_seconds=30)
    try:
        with session_factory() as session:
            row = StrategyRow(
                name="plugin budget",
                chain=CHAIN,
                token_address=TOKEN,
                token_symbol="TEST",
                kind="plugin",
                plugin_id="com.example.budget",
                plugin_version="1.0.0",
                params={
                    "interval_minutes": 60,
                    "max_position_usd": 500,
                    "config": {"entry_pct": 1},
                },
                status="active",
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            decision = await evaluate_plugin_strategy(
                session,
                row,
                stale,
                Runtime(),  # type: ignore[arg-type]
                host,
                registry,
                now=NOW,
            )
    finally:
        await host.close_all()

    # With a stale mark the host passes ``price=None``, so the position cannot be valued
    # and the buy is refused instead of sizing headroom from an old observation.
    assert decision.action == "hold"
    assert decision.reason == "PLUGIN_POSITION_UNKNOWN"
