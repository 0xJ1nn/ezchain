"""One decision window yields one run record and one execution (CONTRACT I9).

Two evaluations of the same strategy use independent sessions and connections; both read
``last_run_at`` before either has committed, so both clear the interval gate. The decision
claim in ``strategy_runs`` is the mechanism of record: at most one run row and one order
may exist for the window, and the caller that loses reports the winner's record instead of
deciding a second time.

Scope of proof: this is same-process asyncio interleaving with two separate SQLite
sessions/connections, not two OS processes and not byte-for-byte simultaneous DB work.
It exercises the unique-key claim after both callers pass the pre-check; the production
guarantee still comes from the database constraint, and Postgres was not driven here.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

import ezchain_api.models  # noqa: F401  (register tables on Base.metadata)
from ezchain_adapters import QuoteRouter
from ezchain_api.config import Settings
from ezchain_api.db import Base
from ezchain_api.models import PaperFillRow, PaperOrderRow, StrategyRow, StrategyRunRow
from ezchain_api.services.executor import PaperOrderExecutor
from ezchain_core.types import PairInfo, SourceRef, TokenSnapshot
from ezchain_engine import RunResult, StrategyScheduler, evaluate_strategy

# A mark-only chain: this test proves the decision claim, not the venue. An executable
# chain would need a live quote provider here and would test something else entirely.
CHAIN = "blast"
TOKEN = "0x0000000000000000000000000000000000000abc"
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def _router(source: StaticDex) -> QuoteRouter:
    return QuoteRouter(providers=[], dexscreener=source)  # type: ignore[arg-type]


def _snapshot() -> TokenSnapshot:
    return TokenSnapshot(
        chain=CHAIN,
        address=TOKEN,
        symbol="TEST",
        name="Test",
        price_usd=Decimal("2"),
        liquidity_usd=Decimal("500000"),
        volume_24h_usd=Decimal("1000000"),
        price_change_m5=Decimal("0"),
        price_change_h1=Decimal("6"),
        price_change_h6=Decimal("0"),
        price_change_h24=Decimal("0"),
        pairs=(
            PairInfo(
                pair_address="0xpair",
                dex="uniswap",
                quote_token="USDC",
                price_usd=Decimal("2"),
                liquidity_usd=Decimal("500000"),
                volume_24h_usd=Decimal("1000000"),
                url=None,
            ),
        ),
        source=SourceRef("dexscreener", "https://dexscreener.com", datetime.now(UTC)),
        pair_created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )


class StaticDex:
    """A deterministic snapshot for the paper mark."""

    def __init__(self) -> None:
        self.snapshot = _snapshot()
        self.calls = 0

    async def get_token(
        self, chain: str, address: str, *, max_age_seconds: float | None = None
    ) -> TokenSnapshot:
        self.calls += 1
        return self.snapshot


class Barrier:
    """Release every caller together once each reaches the same boundary."""

    def __init__(self, parties: int = 2) -> None:
        self._parties = parties
        self._seen = 0
        self._release = asyncio.Event()

    async def wait(self) -> None:
        self._seen += 1
        if self._seen >= self._parties:
            self._release.set()
        await self._release.wait()


class BarrierDex(StaticDex):
    """Hold both evaluators at the market read: past the due gate, before the decision.

    The barrier cannot sit in the executor any more. The evaluator that loses the window
    must never reach the executor, so a barrier there would leave it waiting for a partner
    that is not allowed to arrive.
    """

    def __init__(self, barrier: Barrier) -> None:
        super().__init__()
        self._barrier = barrier

    async def get_token(
        self, chain: str, address: str, *, max_age_seconds: float | None = None
    ) -> TokenSnapshot:
        snapshot = await super().get_token(chain, address, max_age_seconds=max_age_seconds)
        await self._barrier.wait()
        return snapshot


class CountingExecutor:
    """Count executions, not just persisted orders."""

    def __init__(self, inner: PaperOrderExecutor, seen: list[object]) -> None:
        self._inner = inner
        self._seen = seen

    async def place_order(self, request: object) -> str:
        self._seen.append(request)
        return await self._inner.place_order(request)  # type: ignore[arg-type]


def _two_processes(
    url: str,
) -> tuple[Engine, Engine, sessionmaker[Session], sessionmaker[Session]]:
    """Two engines over one database file: two processes, each with its own connections."""

    def _engine() -> Engine:
        engine: Engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _wal(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
            # WAL lets an evaluator's read coexist with the other one's write.
            dbapi_connection.execute("PRAGMA journal_mode=WAL")

        return engine

    eng_a = _engine()
    eng_b = _engine()
    Base.metadata.create_all(eng_a)
    return (
        eng_a,
        eng_b,
        sessionmaker(bind=eng_a, expire_on_commit=False),
        sessionmaker(bind=eng_b, expire_on_commit=False),
    )


def _add_strategy(factory: sessionmaker[Session], strategy_id: str) -> None:
    """A person-created strategy, so its orders are not held for approval (ADR-0041)."""
    with factory() as session:
        session.add(
            StrategyRow(
                id=strategy_id,
                name=strategy_id,
                chain=CHAIN,
                token_address=TOKEN,
                token_symbol="TEST",
                kind="momentum",
                params={"order_usd": 100, "interval_minutes": 60},
                status="active",
                created_by_kind="human",
                created_by_id="deployment",
                created_by_owner="deployment",
            )
        )
        session.commit()


async def _evaluate(
    session_factory: sessionmaker[Session],
    strategy_id: str,
    source: StaticDex,
    executor: object,
) -> RunResult:
    with session_factory() as session:
        strategy = session.get(StrategyRow, strategy_id)
        assert strategy is not None
        return await evaluate_strategy(session, strategy, source, executor, now=NOW)  # type: ignore[arg-type]


def _executor(
    session_factory: sessionmaker[Session], settings: Settings, seen: list[object] | None = None
) -> PaperOrderExecutor | CountingExecutor:
    """The real paper executor over its own mark source, so the barrier counts evaluations.

    The executor reads a mark of its own: if it shared the evaluation's source, its own
    market read would arrive at the barrier and release it early.
    """
    source = StaticDex()
    inner = PaperOrderExecutor(session_factory, settings, source, _router(source))
    return inner if seen is None else CountingExecutor(inner, seen)


async def test_concurrent_evaluation_places_one_order_for_the_window(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'concurrent.db'}"
    eng_a, eng_b, factory_a, factory_b = _two_processes(url)
    _add_strategy(factory_a, "strategy-race")
    settings = Settings(environment="test", database_url=url, data_mode="fixture")
    barrier = Barrier()
    source_a = BarrierDex(barrier)
    source_b = BarrierDex(barrier)
    executor_a = _executor(factory_a, settings)
    executor_b = _executor(factory_b, settings)

    # No exception may escape: the loser of the race resolves to the winning record.
    first, second = await asyncio.gather(
        _evaluate(factory_a, "strategy-race", source_a, executor_a),
        _evaluate(factory_b, "strategy-race", source_b, executor_b),
    )

    with factory_a() as session:
        assert session.scalar(select(func.count()).select_from(PaperOrderRow)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(PaperOrderRow)
                .where(PaperOrderRow.status == "filled")
            )
            == 1
        )
        assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 1
        assert session.scalar(select(func.count()).select_from(StrategyRunRow)) == 1
        order = session.scalar(select(PaperOrderRow))
        assert order is not None
        assert order.strategy_id == "strategy-race"
        # The engine is the placing principal, recorded on the order through the one
        # execution path; a concurrent evaluator cannot re-attribute the winner.
        assert (order.actor_kind, order.actor_id) == ("strategy", "strategy-race")
        order_id = order.id
        run = session.scalar(select(StrategyRunRow))
    assert run is not None

    assert first.action == second.action == "buy"
    assert first.run_id == second.run_id == run.id
    # The loser never claims an order it did not place. It can still be mid-flight when it
    # reports, so the claim it returns may not carry the winner's order id yet; the one
    # order that exists is the winner's.
    assert {first.order_id, second.order_id} - {None} == {order_id}
    assert barrier._seen == 2
    eng_a.dispose()
    eng_b.dispose()


async def test_two_ticks_in_one_window_write_one_run_and_one_order(tmp_path: Path) -> None:
    """Two scheduler ticks released together decide the window exactly once.

    Both passes read ``last_run_at`` before either has committed, so both clear the due
    gate. Before the decision claim each appended its own row and placed its own order (the
    order key collapsed the fill), so the activity log counted two decisions for one order
    and one tick's execution was invisible in the money trail (CONTRACT I9).
    """
    url = f"sqlite+pysqlite:///{tmp_path / 'two-ticks.db'}"
    eng_a, eng_b, factory_a, factory_b = _two_processes(url)
    _add_strategy(factory_a, "strategy-two-ticks")
    settings = Settings(environment="test", database_url=url, data_mode="fixture")
    barrier = Barrier()
    source_a = BarrierDex(barrier)
    source_b = BarrierDex(barrier)
    seen: list[object] = []
    executor_a = _executor(factory_a, settings, seen)
    executor_b = _executor(factory_b, settings, seen)
    tick_a = StrategyScheduler(factory_a, source_a, executor_a, interval_seconds=1, now=lambda: NOW)
    tick_b = StrategyScheduler(factory_b, source_b, executor_b, interval_seconds=1, now=lambda: NOW)

    first, second = await asyncio.gather(tick_a.run_once(), tick_b.run_once())

    with factory_a() as session:
        assert session.scalar(select(func.count()).select_from(StrategyRunRow)) == 1
        assert session.scalar(select(func.count()).select_from(PaperOrderRow)) == 1
        run = session.scalar(select(StrategyRunRow))
    assert run is not None
    assert run.action == "buy"
    # The mechanism is the claim, not the order key: only the owner reaches the executor.
    assert len(seen) == 1
    reported = [*first, *second]
    assert [result.run_id for result in reported] == [run.id, run.id]
    assert {result.order_id for result in reported} - {None} == {run.order_id} - {None}
    assert barrier._seen == 2
    eng_a.dispose()
    eng_b.dispose()


async def test_a_restart_inside_the_window_does_not_decide_again(tmp_path: Path) -> None:
    """A restarted process must not repeat a window the previous process already decided.

    The restarted evaluator still holds the row it read before it died, where
    ``last_run_at`` is None, so its due gate passes and it reaches a decision. The
    committed claim is what keeps the window idempotent across the restart: the second
    call executes nothing and reports the first one's record.
    """
    url = f"sqlite+pysqlite:///{tmp_path / 'restart.db'}"
    eng_a, eng_b, factory_a, factory_b = _two_processes(url)
    _add_strategy(factory_a, "strategy-restart")
    settings = Settings(environment="test", database_url=url, data_mode="fixture")
    source_a = StaticDex()
    source_b = StaticDex()
    seen: list[object] = []
    executor_a = _executor(factory_a, settings, seen)
    executor_b = _executor(factory_b, settings, seen)

    with factory_b() as session:
        # The row this process loaded before it restarted: it cannot know the window is
        # taken, so only the database can tell it.
        stale = session.get(StrategyRow, "strategy-restart")
        assert stale is not None

        first = await StrategyScheduler(
            factory_a, source_a, executor_a, interval_seconds=1, now=lambda: NOW
        ).run_once()
        again = await evaluate_strategy(session, stale, source_b, executor_b, now=NOW)

    with factory_a() as session:
        assert session.scalar(select(func.count()).select_from(StrategyRunRow)) == 1
        assert session.scalar(select(func.count()).select_from(PaperOrderRow)) == 1
        run = session.scalar(select(StrategyRunRow))
    assert run is not None
    assert len(first) == 1
    assert first[0].run_id == again.run_id == run.id
    assert first[0].order_id == again.order_id == run.order_id
    assert len(seen) == 1
    eng_a.dispose()
    eng_b.dispose()
