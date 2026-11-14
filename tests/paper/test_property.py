"""Property test: random buy/sell sequences keep the ledger and positions sound."""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from decimal import Decimal, localcontext

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ezchain_api.models import (
    LedgerEntryRow,
    PaperFillRow,
    PaperOrderRow,
    new_id,
)
from ezchain_api.services import paper
from ezchain_api.services.reconciliation import reconciliation
from ezchain_core import buy_entries, sell_entries
from ezchain_core.ledger import bps_fee
from ezchain_core.money import quantize_money

from .conftest import FakeDexScreener, FakeJupiter, make_request, make_snapshot, place

GENESIS = Decimal("10000")
STEPS = 150
WALK_STEPS = 320
WALK_TOKENS = (
    "0x00000000000000000000000000000000000000a1",
    "0x00000000000000000000000000000000000000b2",
    "0x00000000000000000000000000000000000000c3",
    "0x00000000000000000000000000000000000000d4",
)
MONEY_QUANTUM = Decimal("0.00000001")


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
    # Quantities are exact divisions; summing them at the display precision would round
    # the total and could report a phantom negative dust position. Sum at the ledger's
    # own precision, like ``replay_position`` does.
    with localcontext() as context:
        context.prec = paper.LEDGER_PRECISION
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


@pytest.mark.parametrize("seed", [7, 19])
def test_reconciliation_identity_holds_on_a_multi_token_random_walk(
    session: Session, settings, seed: int
) -> None:
    """The full reconciliation report must be ok after every step of a seeded walk.

    Four tokens at different prices, buys and sells, and a moving mark. The walk writes
    orders, fills and journal groups through the same core entry builders the order
    service uses; it skips the quote/risk layer so 320 reconciliations stay cheap. Every
    step is checked against the endpoint's own report, so a corrupt journal cannot be
    hidden by the test recomputing the same arithmetic a different way.
    """
    rng = random.Random(seed)
    prices = {
        WALK_TOKENS[0]: Decimal("10"),
        WALK_TOKENS[1]: Decimal("2"),
        WALK_TOKENS[2]: Decimal("250"),
        WALK_TOKENS[3]: Decimal("0.5"),
    }
    positions = {token: (Decimal(0), Decimal(0)) for token in WALK_TOKENS}
    filled_sides: Counter[str] = Counter()
    filled_tokens: set[str] = set()
    paper.ensure_genesis(session, settings.paper_starting_cash_usd)

    report: dict | None = None
    for step in range(WALK_STEPS):
        token = rng.choice(WALK_TOKENS)
        drift = Decimal(str(rng.uniform(-0.04, 0.04)))
        price = max(
            Decimal("0.5"),
            (prices[token] * (Decimal(1) + drift)).quantize(Decimal("0.0001")),
        )
        prices[token] = price
        paper.record_snapshot(session, make_snapshot(price=price, address=token))

        held_qty, held_cost = positions[token]
        side = "buy" if rng.random() < 0.55 or held_qty <= 0 else "sell"
        amount = Decimal(rng.randint(5, 150))
        order_id = new_id()
        if side == "buy":
            notional = amount
            quantity = notional / price
            fee = bps_fee(notional, settings.paper_fee_bps)
            entries = buy_entries(
                chain="blast",
                token_address=token,
                quantity=quantity,
                price_usd=price,
                fee_bps=settings.paper_fee_bps,
                notional_usd=notional,
                fee_usd=fee,
            )
            positions[token] = (held_qty + quantity, held_cost + notional)
        else:
            quantity = min(amount / price, held_qty)
            # The order path quantizes a derived USD notional once at the ledger boundary;
            # this walk mirrors it so the persisted fill and journal agree exactly.
            notional = quantize_money(quantity * price)
            fee = bps_fee(notional, settings.paper_fee_bps)
            average = held_cost / held_qty
            basis = (
                held_cost
                if quantity == held_qty
                else min((quantity * average).quantize(MONEY_QUANTUM), held_cost)
            )
            entries = sell_entries(
                chain="blast",
                token_address=token,
                quantity=quantity,
                price_usd=price,
                avg_cost_usd=average,
                fee_bps=settings.paper_fee_bps,
                proceeds_usd=notional,
                cost_basis_usd=basis,
                fee_usd=fee,
            )
            remaining = held_qty - quantity
            positions[token] = (remaining, Decimal(0) if remaining == 0 else held_cost - basis)

        session.add(
            PaperOrderRow(
                id=order_id,
                idempotency_key=f"walk-{seed}-{step}",
                chain="blast",
                token_address=token,
                side=side,
                amount_usd=notional,
                status="filled",
            )
        )
        session.add(
            PaperFillRow(
                id=new_id(),
                order_id=order_id,
                chain="blast",
                token_address=token,
                side=side,
                quantity=quantity,
                price_usd=price,
                notional_usd=notional,
                fee_usd=fee,
                quote_kind="paper_mark",
            )
        )
        for entry in entries:
            session.add(LedgerEntryRow(**entry.to_row(order_id)))
        session.flush()
        filled_sides[side] += 1
        filled_tokens.add(token)

        report = reconciliation(session)
        if not report["ok"]:
            failed = [check for check in report["checks"] if not check["ok"]]
            pytest.fail(f"step {step} ({side} {amount} on {token}): {failed}")

    session.commit()
    assert report is not None
    assert filled_sides["buy"] > 0
    assert filled_sides["sell"] > 0
    assert len(filled_tokens) >= 2
    assert report["totals"]["fills"] == sum(filled_sides.values())
    assert report["totals"]["orphan_fills"] == 0
    assert report["totals"]["filled_orders_without_single_fill"] == 0
