"""PAPER performance is attributed per strategy, from the account's ledger rows.

The strategy endpoint is a scoped view of the one PnL projector: it reads the same
``ledger_entries`` as account performance, filtered to the orders attributed to the
strategy. A strategy never reads a private fill loop that books fees differently.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ezchain_api.models import (
    LedgerEntryRow,
    MarketSnapshotRow,
    PaperFillRow,
    PaperOrderRow,
    StrategyRow,
)
from ezchain_api.routers import account as account_router
from ezchain_api.routers import strategies
from ezchain_core import buy_entries, sell_entries

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
    avg_cost: str | None = None,
    basis: str | None = None,
) -> None:
    """Persist one filled order exactly as the order service journals it.

    The order, fill and ledger group are written together, so the strategy surface and
    account performance read the same rows. A sell states the ledger's exact cost basis;
    deriving it from a different formula here would hide the projector under test.
    """
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
    qty = Decimal(quantity)
    if side == "buy":
        entries = buy_entries(
            chain=CHAIN,
            token_address=TOKEN,
            quantity=qty,
            price_usd=Decimal(price),
            fee_bps=0,
            notional_usd=Decimal(notional),
            fee_usd=Decimal(fee),
        )
    else:
        assert avg_cost is not None, "a sell fixture must state the ledger cost basis"
        cost_basis = Decimal(basis) if basis is not None else qty * Decimal(avg_cost)
        entries = sell_entries(
            chain=CHAIN,
            token_address=TOKEN,
            quantity=qty,
            price_usd=Decimal(price),
            avg_cost_usd=Decimal(avg_cost),
            fee_bps=0,
            proceeds_usd=Decimal(notional),
            cost_basis_usd=cost_basis,
            fee_usd=Decimal(fee),
        )
    session.add(
        PaperFillRow(
            order_id=order.id,
            chain=CHAIN,
            token_address=TOKEN,
            side=side,
            quantity=qty,
            price_usd=Decimal(price),
            notional_usd=Decimal(notional),
            fee_usd=Decimal(fee),
            quote_kind="paper_mark",
            created_at=at,
        )
    )
    for entry in entries:
        session.add(LedgerEntryRow(**entry.to_row(order.id, created_at=at)))
    session.commit()


def _token_row(body: dict, token: str = TOKEN) -> dict:
    return next(row for row in body["by_token"] if row["token_address"] == token)


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
        avg_cost="1",
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
    # The ledger books the buy fee when it is paid: the buy group is realized -1.00 and
    # the sell group is 80 - 40 * 1 - 0.8 = 39.20, so the two groups net to 38.20.
    assert Decimal(body["realized_pnl_usd"]) == Decimal("38.2")
    # The remaining 60 units still carry their cash cost, 60, not a fee-loaded basis.
    assert Decimal(body["avg_cost_usd"]) == Decimal("1")
    assert Decimal(body["open_quantity"]) == Decimal("60")
    assert Decimal(body["fees_usd"]) == Decimal("1.8")
    # Mark 1.5: 90 - 60 = 30.
    assert Decimal(body["unrealized_pnl_usd"]) == Decimal("30")
    assert Decimal(body["market_value_usd"]) == Decimal("90")
    assert body["complete"] is True
    assert body["marks_unknown"] is False
    assert body["mark_usd"] == "1.5"
    assert len(body["equity_curve"]) == 2
    assert [Decimal(point["pnl_usd"]) for point in body["equity_curve"]] == [
        Decimal("-1"),
        Decimal("38.2"),
    ]


def test_account_and_strategy_agree_on_a_partial_close(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    """A buy fee and a partial close used to split the same PnL two ways.

    The account projector booked the whole buy fee at the buy (realized -1.00) and
    valued the remaining cost basis at 60. The strategy loop capitalised the buy fee
    into the basis (1.01 per unit) and reported 38.80 realized with 29.40 unrealized.
    Both now report the ledger's result: 38.20 realized and 30.00 unrealized.
    """
    own = strategy(session, "own")
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
        avg_cost="1",
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

    account = client_for(account_router.router).get("/api/v1/paper/performance").json()
    paper_strategy = (
        client_for(strategies.router).get(f"/api/v1/strategies/{own.id}/performance").json()
    )
    token = _token_row(account)

    assert token["realized_usd"] == paper_strategy["realized_pnl_usd"] == "38.2"
    assert token["fees_usd"] == paper_strategy["fees_usd"] == "1.8"
    assert token["market_value_usd"] == paper_strategy["market_value_usd"] == "90"
    assert token["unrealized_usd"] == paper_strategy["unrealized_pnl_usd"] == "30"
    assert token["quantity"] == paper_strategy["open_quantity"] == "60"
    assert token["avg_cost_usd"] == paper_strategy["avg_cost_usd"] == "1"
    assert account["complete"] is paper_strategy["complete"] is True
    assert account["marks_unknown"] is paper_strategy["marks_unknown"] is False
    # The account total and the strategy total are the same rows, so they agree.
    assert account["realized_usd"] == paper_strategy["realized_pnl_usd"]
    assert account["fees_usd"] == paper_strategy["fees_usd"]


def test_unknown_mark_is_incomplete_on_both_surfaces(
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

    account = client_for(account_router.router).get("/api/v1/paper/performance").json()
    body = client_for(strategies.router).get(f"/api/v1/strategies/{own.id}/performance").json()

    assert account["complete"] is False
    assert account["marks_unknown"] is True
    assert account["equity_usd"] is None
    assert account["unrealized_usd"] is None
    token = _token_row(account)
    assert token["market_value_usd"] is None
    assert token["unrealized_usd"] is None

    assert body["mark_usd"] is None
    assert body["unrealized_pnl_usd"] is None
    assert body["market_value_usd"] is None
    assert body["complete"] is False
    assert body["marks_unknown"] is True
    # The bought units and the paid fee are known; only the mark is unknown.
    assert body["open_quantity"] == "10"
    assert body["avg_cost_usd"] == "1"
    assert body["realized_pnl_usd"] == "-0.1"


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
        avg_cost="1",
    )
    client: TestClient = client_for(strategies.router)

    body = client.get(f"/api/v1/strategies/{own.id}/performance").json()

    assert body["open_quantity"] == "0"
    assert body["avg_cost_usd"] is None
    assert body["unrealized_pnl_usd"] == "0"
    assert body["market_value_usd"] == "0"
    assert body["complete"] is True
    assert body["marks_unknown"] is False
    # 20 - 10 basis - 0.2 fee, less the 0.1 buy fee already booked.
    assert Decimal(body["realized_pnl_usd"]) == Decimal("9.7")


def test_account_and_strategy_agree_across_a_seeded_random_walk(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    """Both entry points must read one ledger through one projector for 120 steps."""
    steps = 120
    rng = random.Random(20261110)
    own = strategy(session, "walk")
    snapshot = MarketSnapshotRow(
        chain=CHAIN,
        token_address=TOKEN,
        symbol="TEST",
        name="Test",
        price_usd=Decimal("10"),
        source="test",
        observed_at=BASE,
    )
    session.add(snapshot)
    session.commit()

    account_client: TestClient = client_for(account_router.router)
    strategy_client: TestClient = client_for(strategies.router)
    price = Decimal("10")
    quantity = Decimal(0)
    cost = Decimal(0)

    for step in range(steps):
        drift = Decimal(str(rng.uniform(-0.04, 0.04)))
        price = max(Decimal("0.5"), (price * (Decimal(1) + drift)).quantize(Decimal("0.0001")))
        snapshot.price_usd = price
        snapshot.observed_at = BASE + timedelta(minutes=step + 1)
        session.commit()

        at = BASE + timedelta(minutes=step + 1)
        open_side = quantity <= 0 or rng.random() < 0.45
        if open_side:
            notional = Decimal(rng.randint(20, 200))
            qty = (notional / price).quantize(Decimal("0.00000001"))
            notional = (qty * price).quantize(Decimal("0.00000001"))
            fee = (notional * Decimal(str(rng.choice([0, 0.001, 0.003])))).quantize(
                Decimal("0.00000001")
            )
            fill(
                session,
                strategy_id=own.id,
                side="buy",
                quantity=str(qty),
                price=str(price),
                notional=str(notional),
                fee=str(fee),
                at=at,
            )
            quantity += qty
            cost += notional
        else:
            fraction = Decimal(str(rng.uniform(0.25, 1.0)))
            qty = (quantity * fraction).quantize(Decimal("0.00000001"))
            if qty >= quantity:
                qty = quantity
                basis = cost
            else:
                basis = min((qty * (cost / quantity)).quantize(Decimal("0.00000001")), cost)
            notional = (qty * price).quantize(Decimal("0.00000001"))
            fee = (notional * Decimal(str(rng.choice([0, 0.001, 0.003])))).quantize(
                Decimal("0.00000001")
            )
            fill(
                session,
                strategy_id=own.id,
                side="sell",
                quantity=str(qty),
                price=str(price),
                notional=str(notional),
                fee=str(fee),
                at=at,
                avg_cost=str(cost / quantity),
                basis=str(basis),
            )
            quantity -= qty
            cost -= basis
            if quantity <= 0:
                quantity = Decimal(0)
                cost = Decimal(0)

        account = account_client.get("/api/v1/paper/performance").json()
        paper_strategy = strategy_client.get(f"/api/v1/strategies/{own.id}/performance").json()
        token = _token_row(account)

        assert token["realized_usd"] == paper_strategy["realized_pnl_usd"], f"step {step}"
        assert token["fees_usd"] == paper_strategy["fees_usd"], f"step {step}"
        assert token["market_value_usd"] == paper_strategy["market_value_usd"], f"step {step}"
        assert token["unrealized_usd"] == paper_strategy["unrealized_pnl_usd"], f"step {step}"
        assert token["quantity"] == paper_strategy["open_quantity"], f"step {step}"
        assert token["avg_cost_usd"] == paper_strategy["avg_cost_usd"], f"step {step}"
        assert token["mark_usd"] == paper_strategy["mark_usd"], f"step {step}"
        assert account["wins"] == paper_strategy["wins"], f"step {step}"
        assert account["losses"] == paper_strategy["losses"], f"step {step}"
        assert account["complete"] is paper_strategy["complete"] is True, f"step {step}"
        assert account["realized_usd"] == paper_strategy["realized_pnl_usd"], f"step {step}"
        assert account["fees_usd"] == paper_strategy["fees_usd"], f"step {step}"

    fills = session.scalar(select(func.count()).select_from(PaperFillRow))
    assert fills == steps


def test_unknown_strategy_is_404(client_for: Callable[..., TestClient]) -> None:
    client: TestClient = client_for(strategies.router)
    assert client.get("/api/v1/strategies/nope/performance").status_code == 404
