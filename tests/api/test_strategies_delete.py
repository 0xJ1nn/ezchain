"""Regression: a strategy with run history can be deleted (issue #61 stress cleanup)."""

from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ezchain_api.models import Base, StrategyRow, StrategyRunRow
from ezchain_api.routers.strategies import delete_strategy


def test_delete_strategy_removes_its_runs() -> None:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        strategy = StrategyRow(
            name="stress test",
            chain="base",
            token_address="0x00000000000000000000000000000000000000aa",
            token_symbol="TEST",
            kind="dca",
            params={"order_usd": "1"},
            status="active",
        )
        session.add(strategy)
        session.flush()
        session.add(
            StrategyRunRow(
                strategy_id=strategy.id,
                action="hold",
                reason="INTERVAL_NOT_ELAPSED",
                inputs={},
            )
        )
        session.commit()
        strategy_id = strategy.id

        delete_strategy(strategy_id, session)

        assert session.get(StrategyRow, strategy_id) is None
        assert (
            session.scalars(
                select(StrategyRunRow).where(StrategyRunRow.strategy_id == strategy_id)
            ).all()
            == []
        )
