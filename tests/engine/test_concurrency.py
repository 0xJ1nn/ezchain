"""Concurrent run-now and scheduler evaluation share one idempotency window.

Two evaluations of the same strategy use independent sessions and connections and are
released together at the order-execution boundary. The unique idempotency key in
``paper_orders`` is the mechanism of record: at most one order and one fill may exist for
the window.

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
from ezchain_api.config import Settings
from ezchain_api.db import Base
from ezchain_api.models import PaperFillRow, PaperOrderRow, StrategyRow
from ezchain_api.services.executor import PaperOrderExecutor
from ezchain_core.types import PairInfo, SourceRef, TokenSnapshot
from ezchain_engine import evaluate_strategy

CHAIN = "base"
TOKEN = "0x0000000000000000000000000000000000000abc"


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
    )


class StaticDex:
    """A deterministic snapshot for the paper mark."""

    def __init__(self) -> None:
        self.snapshot = _snapshot()
        self.calls = 0

    async def get_token(self, chain: str, address: str) -> TokenSnapshot:
        self.calls += 1
        return self.snapshot


class Barrier:
    """Release both callers together once each reaches the execution boundary."""

    def __init__(self, parties: int = 2) -> None:
        self._parties = parties
        self._seen = 0
        self._release = asyncio.Event()

    async def wait(self) -> None:
        self._seen += 1
        if self._seen >= self._parties:
            self._release.set()
        await self._release.wait()


class BarrierExecutor:
    """Hold both executions until each has made its decision, then let them race."""

    def __init__(self, inner: PaperOrderExecutor, barrier: Barrier) -> None:
        self._inner = inner
        self._barrier = barrier

    async def place_order(self, request: object) -> str:
        await self._barrier.wait()
        return await self._inner.place_order(request)  # type: ignore[arg-type]


async def _evaluate(
    session_factory: sessionmaker[Session],
    strategy_id: str,
    source: StaticDex,
    executor: BarrierExecutor,
    now: datetime,
) -> object:
    with session_factory() as session:
        strategy = session.get(StrategyRow, strategy_id)
        assert strategy is not None
        return await evaluate_strategy(session, strategy, source, executor, now=now)


async def test_concurrent_evaluation_places_one_order_for_the_window(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'concurrent.db'}"

    def _engine() -> Engine:
        eng: Engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(eng, "connect")
        def _wal(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
            # WAL lets the evaluator's read transaction coexist with the order writer.
            dbapi_connection.execute("PRAGMA journal_mode=WAL")

        return eng

    eng_a = _engine()
    eng_b = _engine()
    Base.metadata.create_all(eng_a)
    factory_a = sessionmaker(bind=eng_a, expire_on_commit=False)
    factory_b = sessionmaker(bind=eng_b, expire_on_commit=False)
    settings = Settings(environment="test", database_url=url, data_mode="fixture")

    with factory_a() as session:
        strategy = StrategyRow(
            id="strategy-race",
            name="race",
            chain=CHAIN,
            token_address=TOKEN,
            token_symbol="TEST",
            kind="momentum",
            params={"order_usd": 100, "interval_minutes": 60},
            status="active",
        )
        session.add(strategy)
        session.commit()

    source_a = StaticDex()
    source_b = StaticDex()
    barrier = Barrier()
    executor_a = BarrierExecutor(
        PaperOrderExecutor(factory_a, settings, source_a, None),  # type: ignore[arg-type]
        barrier,
    )
    executor_b = BarrierExecutor(
        PaperOrderExecutor(factory_b, settings, source_b, None),  # type: ignore[arg-type]
        barrier,
    )
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)

    # No exception may escape: the loser of the race must resolve to the winning order.
    first, second = await asyncio.gather(
        _evaluate(factory_a, "strategy-race", source_a, executor_a, now),
        _evaluate(factory_b, "strategy-race", source_b, executor_b, now),
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
        order = session.scalar(select(PaperOrderRow))
        assert order is not None
        assert order.strategy_id == "strategy-race"
        order_id = order.id

    assert first.action == second.action == "buy"  # type: ignore[attr-defined]
    assert first.order_id == second.order_id == order_id  # type: ignore[attr-defined]
    assert barrier._seen == 2
    eng_a.dispose()
    eng_b.dispose()
