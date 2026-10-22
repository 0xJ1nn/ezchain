"""PAPER performance is attributed per strategy, from its own fills only."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ezchain_api.models import (
    MarketSnapshotRow,
    PaperFillRow,
    PaperOrderRow,
    StrategyRow,
)
from ezchain_api.routers import strategies

CHAIN = "solana"
TOKEN = "So11111111111111111111111111111111111111112"
BASE = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def strategy(session: Session, name: str) -> StrategyRow:
    row = StrategyRow(
        name=name,
        chain=CHAIN,
        token_address=TOKEN,
        token_symbol="TEST",
        kind="dca",
        params={"order_usd": 10, "interval_minutes": 60, "max_position_usd": 100},
        status="active",
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def fill(
    session: Session,
    *,
    strategy_id: str | None,
    side: str,
    quantity: str,
    price: str,
    notional: str,
    fee: str,
    at: datetime,
) -> None:
    order = PaperOrderRow(
        idempotency_key=f"key-{strategy_id}-{at.timestamp()}-{side}",
        chain=CHAIN,
        token_address=TOKEN,
        side=side,
        amount_usd=Decimal(notional),
        status="filled",
        strategy_id=strategy_id,
        created_at=at,
        filled_at=at,
    )
    session.add(order)
    session.flush()
    session.add(
        PaperFillRow(
            order_id=order.id,
            chain=CHAIN,
            token_address=TOKEN,
            side=side,
            quantity=Decimal(quantity),
            price_usd=Decimal(price),
            notional_usd=Decimal(notional),
            fee_usd=Decimal(fee),
            quote_kind="paper_mark",
            created_at=at,
        )
    )
    session.commit()


def test_performance_uses_only_the_strategys_own_fills(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    own = strategy(session, "own")
    other = strategy(session, "other")
    fill(
        session,
        strategy_id=own.id,
        side="buy",
        quantity="100",
        price="1",
        notional="100",
        fee="1",
        at=BASE,
    )
    fill(
        session,
        strategy_id=own.id,
        side="sell",
        quantity="40",
        price="2",
        notional="80",
        fee="0.8",
        at=BASE + timedelta(minutes=5),
    )
    # Another strategy's fills, and a manual fill, must not leak into this curve.
    fill(
        session,
        strategy_id=other.id,
        side="buy",
        quantity="10",
        price="1",
        notional="10",
        fee="0.1",
        at=BASE,
    )
    fill(
        session,
        strategy_id=None,
        side="buy",
        quantity="10",
        price="1",
        notional="10",
        fee="0.1",
        at=BASE,
    )
    session.add(
        MarketSnapshotRow(
            chain=CHAIN,
            token_address=TOKEN,
            symbol="TEST",
            name="Test",
            price_usd=Decimal("1.5"),
            source="dexscreener",
            observed_at=BASE + timedelta(minutes=10),
        )
    )
    session.commit()

    client: TestClient = client_for(strategies.router)
    response = client.get(f"/api/v1/strategies/{own.id}/performance")

    assert response.status_code == 200
    body = response.json()
    assert body["label"] == "PAPER"
    assert body["trades"] == 1
    assert body["wins"] == 1
    assert body["losses"] == 0
    assert body["win_rate_pct"] == "100"
    # Buy basis includes the fee: 101 / 100 = 1.01 per unit.
    assert Decimal(body["avg_cost_usd"]) == Decimal("1.01")
    assert Decimal(body["open_quantity"]) == Decimal("60")
    assert Decimal(body["fees_usd"]) == Decimal("1.8")
    # 80 proceeds - 40 * 1.01 basis - 0.8 fee = 38.8.
    assert Decimal(body["realized_pnl_usd"]) == Decimal("38.8")
    # Mark 1.5: 90 - 60 * 1.01 = 29.4.
    assert Decimal(body["unrealized_pnl_usd"]) == Decimal("29.4")
    assert body["mark_usd"] == "1.5"
    assert len(body["equity_curve"]) == 2
    assert [point["pnl_usd"] for point in body["equity_curve"]] == ["-1", "37.80"]


def test_unknown_mark_keeps_unrealized_null(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    own = strategy(session, "own")
    fill(
        session,
        strategy_id=own.id,
        side="buy",
        quantity="10",
        price="1",
        notional="10",
        fee="0.1",
        at=BASE,
    )
    client: TestClient = client_for(strategies.router)

    response = client.get(f"/api/v1/strategies/{own.id}/performance")

    assert response.status_code == 200
    body = response.json()
    assert body["mark_usd"] is None
    assert body["unrealized_pnl_usd"] is None
    assert body["realized_pnl_usd"] == "0"


def test_flat_position_is_zero_unrealized_not_unknown(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    own = strategy(session, "own")
    fill(
        session,
        strategy_id=own.id,
        side="buy",
        quantity="10",
        price="1",
        notional="10",
        fee="0.1",
        at=BASE,
    )
    fill(
        session,
        strategy_id=own.id,
        side="sell",
        quantity="10",
        price="2",
        notional="20",
        fee="0.2",
        at=BASE + timedelta(minutes=5),
    )
    client: TestClient = client_for(strategies.router)

    body = client.get(f"/api/v1/strategies/{own.id}/performance").json()

    assert body["open_quantity"] == "0"
    assert body["avg_cost_usd"] is None
    assert body["unrealized_pnl_usd"] == "0"
    # 20 - 10.1 basis - 0.2 fee.
    assert Decimal(body["realized_pnl_usd"]) == Decimal("9.7")


def test_unknown_strategy_is_404(client_for: Callable[..., TestClient]) -> None:
    client: TestClient = client_for(strategies.router)
    assert client.get("/api/v1/strategies/nope/performance").status_code == 404
