"""Property test: random buy/sell sequences keep the ledger and positions sound."""

from __future__ import annotations

import random
from collections import defaultdict
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ezchain_api.models import LedgerEntryRow, PaperFillRow, PaperOrderRow
from ezchain_api.services import paper

from .conftest import FakeDexScreener, FakeJupiter, make_request, make_snapshot, place

GENESIS = Decimal("10000")
STEPS = 150


def _group_sums(session: Session) -> dict[str, Decimal]:
    sums: dict[str, Decimal] = defaultdict(Decimal)
    for row in session.scalars(select(LedgerEntryRow)).all():
        sums[row.group_id] += row.usd_value
    return sums


def _position_quantities(session: Session) -> dict[str, Decimal]:
    balances: dict[str, Decimal] = defaultdict(Decimal)
    rows = session.scalars(
        select(LedgerEntryRow).where(LedgerEntryRow.account.like("position:%"))
    ).all()
    for row in rows:
        balances[row.account] += row.amount
    return balances


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_random_buy_sell_sequences_keep_invariants(session: Session, settings, seed: int) -> None:
    rng = random.Random(seed)
    price = Decimal("10")
    dex = FakeDexScreener(make_snapshot(price=price, liquidity=Decimal("1000000")))
    jupiter = FakeJupiter(price=price)

    for step in range(STEPS):
        drift = Decimal(str(rng.uniform(-0.04, 0.04)))
        price = max(Decimal("0.5"), (price * (Decimal(1) + drift)).quantize(Decimal("0.0001")))
        dex.set(make_snapshot(price=price, liquidity=Decimal("1000000")))

        side = "buy" if rng.random() < 0.6 else "sell"
        amount = Decimal(rng.randint(5, 200))
        order = place(
            session,
            settings,
            dex,
            jupiter,
            make_request(side=side, amount_usd=str(amount), key=f"prop-{seed}-{step}"),
        )
        assert order.status in {"filled", "rejected"}
        if order.status == "rejected":
            assert order.reason

        account = paper.account_snapshot(session)
        cash = Decimal(account["cash_usd"])
        assert cash >= 0, f"cash went negative at step {step}: {cash}"

        for account_name, quantity in _position_quantities(session).items():
            assert quantity >= 0, f"negative position {account_name} at step {step}"

        if account["positions"]:
            equity = Decimal(account["equity_usd"])
            realized = Decimal(account["realized_usd"])
            unrealized = Decimal(account["unrealized_usd"])
            # Division/multiplication rounds at 28 significant digits, so reconcile to
            # one ten-thousandth of a cent rather than bit-exact equality.
            assert abs(equity - (GENESIS + realized + unrealized)) < Decimal("0.0001")

        if step % 25 == 0:
            assert all(total == 0 for total in _group_sums(session).values())

    sums = _group_sums(session)
    assert all(total == 0 for total in sums.values())
    filled = session.scalar(
        select(func.count()).select_from(PaperOrderRow).where(PaperOrderRow.status == "filled")
    )
    fills = session.scalar(select(func.count()).select_from(PaperFillRow))
    assert filled == fills
    assert fills is not None and fills > 0
