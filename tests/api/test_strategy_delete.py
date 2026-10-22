"""Strategy deletion cascades its run log and preserves historical order attribution."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ezchain_api.models import PaperOrderRow, StrategyRow, StrategyRunRow
from ezchain_api.routers import strategies


def _seed(session: Session) -> tuple[str, str]:
    strategy = StrategyRow(
        id="strategy-delete-me",
        name="delete me",
        chain="solana",
        token_address="So11111111111111111111111111111111111111112",
        token_symbol="TEST",
        kind="dca",
        params={"order_usd": 10, "interval_minutes": 60, "max_position_usd": 100},
        status="active",
    )
    order = PaperOrderRow(
        id="order-historical",
        idempotency_key="strategy:strategy-delete-me:1",
        chain="solana",
        token_address="So11111111111111111111111111111111111111112",
        side="buy",
        amount_usd=Decimal("10"),
        status="filled",
        strategy_id=strategy.id,
        created_at=datetime(2026, 10, 22, 12, 0, tzinfo=UTC),
    )
    session.add_all(
        [
            strategy,
            order,
            StrategyRunRow(
                id="run-1",
                strategy_id=strategy.id,
                action="buy",
                reason="DCA_INTERVAL",
                inputs={},
                order_id=order.id,
            ),
            StrategyRunRow(
                id="run-2",
                strategy_id=strategy.id,
                action="hold",
                reason="INTERVAL_NOT_ELAPSED",
                inputs={},
            ),
        ]
    )
    session.commit()
    return strategy.id, order.id


def test_delete_removes_runs_and_keeps_order_attribution(client_for, session: Session) -> None:
    strategy_id, order_id = _seed(session)
    client: TestClient = client_for(strategies.router)

    response = client.delete(f"/api/v1/strategies/{strategy_id}")

    assert response.status_code == 204
    assert session.get(StrategyRow, strategy_id) is None
    assert (
        session.scalar(
            select(func.count())
            .select_from(StrategyRunRow)
            .where(StrategyRunRow.strategy_id == strategy_id)
        )
        == 0
    )
    order = session.get(PaperOrderRow, order_id)
    assert order is not None
    assert order.strategy_id == strategy_id


def test_delete_missing_strategy_is_404(client_for, session: Session) -> None:
    client: TestClient = client_for(strategies.router)
    response = client.delete("/api/v1/strategies/not-there")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "STRATEGY_NOT_FOUND"
