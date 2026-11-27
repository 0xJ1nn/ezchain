"""Property tests for idempotency, reduce-only exits and the kill switch.

Each seed is a deterministic scenario. The assertions read the persisted orders, fills and
ledger rather than trusting the order service's return value.
"""

from __future__ import annotations

import asyncio
import random
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from ezchain_api.models import LedgerEntryRow, PaperFillRow, PaperOrderRow
from ezchain_api.services import paper
from ezchain_api.services.paper import IdempotencyConflict
from ezchain_api.services.system_flags import set_kill_switch
from ezchain_core import OrderRequest
from ezchain_core.ledger import entry_from_row, position_account, replay_position
from ezchain_engine.scheduler import ExecutionBlocked, KillSwitchExecutor
from tests.paper.conftest import CHAIN, TOKEN, make_request, make_snapshot, place

SOLANA = "solana"
MINT = "So11111111111111111111111111111111111111112"
SOLANA_ACCOUNT = position_account(SOLANA, MINT)


def _count(session, model) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _position_quantity(session) -> Decimal:
    rows = session.scalars(
        select(LedgerEntryRow).where(LedgerEntryRow.account == SOLANA_ACCOUNT)
    ).all()
    if not rows:
        return Decimal(0)
    quantity, _cost = replay_position([entry_from_row(row) for row in rows])
    return quantity


@pytest.mark.parametrize("seed", range(8))
def test_one_idempotency_key_never_fills_twice(session, settings, dex, jupiter, seed: int) -> None:
    rng = random.Random(seed)
    key = f"prop-idem-{seed:02d}"
    amount = Decimal(rng.randint(10, 200))

    orders = [
        place(session, settings, dex, jupiter, make_request(amount_usd=str(amount), key=key))
        for _ in range(4)
    ]
    assert len({order.id for order in orders}) == 1
    assert all(order.status == "filled" for order in orders)
    assert _count(session, PaperOrderRow) == 1
    assert _count(session, PaperFillRow) == 1

    # The same key naming a different payload is a conflict, never a second fill.
    with pytest.raises(IdempotencyConflict):
        place(
            session,
            settings,
            dex,
            jupiter,
            make_request(amount_usd=str(amount + 1), key=key),
        )
    assert _count(session, PaperOrderRow) == 1
    assert _count(session, PaperFillRow) == 1


@pytest.mark.parametrize("seed", range(6))
def test_reduce_only_exits_never_grow_a_position(
    session, settings, dex, jupiter, seed: int
) -> None:
    rng = random.Random(seed)
    jupiter.price = Decimal("2")
    dex.set(
        make_snapshot(
            price=Decimal("2"),
            liquidity=Decimal("500000"),
            chain=SOLANA,
            address=MINT,
        )
    )
    opened = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(
            side="buy",
            amount_usd="100",
            key=f"prop-exit-open-{seed}",
            chain=SOLANA,
            token=MINT,
        ),
    )
    assert opened.status == "filled"
    previous = _position_quantity(session)
    assert previous > 0

    for step in range(10):
        exit_order = place(
            session,
            settings,
            dex,
            jupiter,
            make_request(
                side="sell",
                amount_usd=str(rng.randint(5, 500)),
                key=f"prop-exit-{seed}-{step}",
                chain=SOLANA,
                token=MINT,
                reduce_only=True,
            ),
        )
        current = _position_quantity(session)
        assert 0 <= current <= previous, (
            f"seed {seed} step {step}: exit {exit_order.status} grew the position "
            f"from {previous} to {current}"
        )
        previous = current
        if previous == 0:
            break

    fills = _count(session, PaperFillRow)
    filled = session.scalar(
        select(func.count()).select_from(PaperOrderRow).where(PaperOrderRow.status == "filled")
    )
    assert fills == filled


@pytest.mark.parametrize("seed", range(8))
def test_an_armed_switch_refuses_every_new_entry(
    session, settings, dex, jupiter, seed: int
) -> None:
    rng = random.Random(seed)
    paper.ensure_genesis(session, settings.paper_starting_cash_usd)
    set_kill_switch(session, active=True, reason="property drill")
    journal_before = _count(session, LedgerEntryRow)

    for step in range(6):
        order = place(
            session,
            settings,
            dex,
            jupiter,
            make_request(
                amount_usd=str(rng.randint(10, 200)),
                key=f"prop-kill-{seed}-{step}",
                strategy_id="strategy-1" if rng.random() < 0.5 else None,
            ),
        )
        assert order.status == "rejected"
        assert order.reason in {"KILL_SWITCH_ARMED", "KILL_SWITCH_UNAVAILABLE"}

    assert _count(session, PaperFillRow) == 0
    assert _count(session, LedgerEntryRow) == journal_before


def test_the_executor_guard_refuses_every_strategy_order() -> None:
    class RecordingExecutor:
        def __init__(self) -> None:
            self.calls: list[OrderRequest] = []

        async def place_order(self, request: OrderRequest) -> str:
            self.calls.append(request)
            return "would-have-filled"

    inner = RecordingExecutor()
    executor = KillSwitchExecutor(inner, lambda: True)
    rng = random.Random(41)
    for step in range(25):
        request = OrderRequest(
            chain=CHAIN,
            token_address=TOKEN,
            side=rng.choice(["buy", "sell"]),
            amount_usd=Decimal(rng.randint(10, 500)),
            idempotency_key=f"prop-executor-{step:02d}",
            strategy_id=f"strategy-{step}",
        )
        with pytest.raises(ExecutionBlocked):
            asyncio.run(executor.place_order(request))
    assert inner.calls == []
