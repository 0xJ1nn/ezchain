"""A strategy's trade-policy profile reaches the order gate, or it inherits the default."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from helpers import make_snapshot
from sqlalchemy.orm import Session, sessionmaker

from ezchain_api.models import StrategyRow
from ezchain_core.types import OrderRequest, TokenSnapshot
from ezchain_engine import evaluate_strategy


class FakeSource:
    def __init__(self, snapshot: TokenSnapshot | None = None) -> None:
        self.snapshot = snapshot or make_snapshot()

    async def get_token(
        self, chain: str, address: str, *, max_age_seconds: float | None = None
    ) -> TokenSnapshot:
        return self.snapshot


class FakeExecutor:
    def __init__(self) -> None:
        self.requests: list[OrderRequest] = []

    async def place_order(self, request: OrderRequest) -> str:
        self.requests.append(request)
        return "order-1"


async def _run(
    session_factory: sessionmaker[Session], strategy_id: str, executor: FakeExecutor
) -> None:
    with session_factory() as session:
        strategy = session.get(StrategyRow, strategy_id)
        assert strategy is not None
        await evaluate_strategy(
            session,
            strategy,
            FakeSource(make_snapshot(change_h1="6")),
            executor,
            now=datetime(2026, 10, 22, 12, 0, tzinfo=UTC),
        )


async def test_a_strategy_profile_reaches_the_order_request(
    session_factory: sessionmaker[Session], add_strategy: Any
) -> None:
    strategy_id = add_strategy(risk_profile="high")
    executor = FakeExecutor()

    await _run(session_factory, strategy_id, executor)

    assert len(executor.requests) == 1
    assert executor.requests[0].risk_profile == "high"


async def test_a_strategy_without_an_override_inherits_the_default(
    session_factory: sessionmaker[Session], add_strategy: Any
) -> None:
    strategy_id = add_strategy()
    executor = FakeExecutor()

    await _run(session_factory, strategy_id, executor)

    assert len(executor.requests) == 1
    assert executor.requests[0].risk_profile is None
