"""Property tests for the append-only ledger.

Hypothesis and Schemathesis are not installed or pinned in this repository, so these are
seeded random walks: every seed is a deterministic case, replayable from its number. The
assertions check the invariants themselves, not a second copy of the journal arithmetic.
"""

from __future__ import annotations

import random
from dataclasses import replace
from decimal import Decimal

import pytest

from ezchain_core import (
    LedgerEntry,
    LedgerError,
    LedgerTamperedError,
    assert_balanced,
    buy_entries,
    position_account,
    replay_balances,
    replay_position,
    seal_entries,
    sell_entries,
    verify_seal,
)
from ezchain_core.execution import Fill, journal_from_fill
from ezchain_core.money import to_minor_units

CHAIN = "blast"
TOKEN = "0x0000000000000000000000000000000000000abc"
ACCOUNT = position_account(CHAIN, TOKEN)


def _quantity(rng: random.Random) -> Decimal:
    return Decimal(rng.randint(1, 10_000)) / Decimal(10 ** rng.randint(0, 6))


def _price(rng: random.Random) -> Decimal:
    return Decimal(rng.randint(1, 10_000)) / Decimal(100)


@pytest.mark.parametrize("seed", range(20))
def test_every_random_journal_balances_to_the_minor_unit(seed: int) -> None:
    rng = random.Random(seed)
    tape: list[LedgerEntry] = []
    for _step in range(40):
        quantity = _quantity(rng)
        price = _price(rng)
        fee_bps = rng.choice([0, 1, 5, 30, 100])
        if rng.random() < 0.55:
            entries = buy_entries(
                chain=CHAIN,
                token_address=TOKEN,
                quantity=quantity,
                price_usd=price,
                fee_bps=fee_bps,
            )
        else:
            entries = sell_entries(
                chain=CHAIN,
                token_address=TOKEN,
                quantity=quantity,
                price_usd=price,
                avg_cost_usd=_price(rng),
                fee_bps=fee_bps,
            )
        assert_balanced(entries)
        assert sum(to_minor_units(entry.usd_value) for entry in entries) == 0
        assert sum(balance.usd_minor for balance in replay_balances(entries)) == 0
        tape.extend(entries)

    # The same identity holds for the whole tape, not only for each order group.
    assert sum(balance.usd_minor for balance in replay_balances(tape)) == 0
    seal = seal_entries(tape)
    verify_seal(tape, seal)
    assert seal_entries(tape) == seal


def test_a_transaction_that_does_not_balance_is_refused() -> None:
    entries = buy_entries(
        chain=CHAIN,
        token_address=TOKEN,
        quantity=Decimal("10"),
        price_usd=Decimal("2"),
        fee_bps=30,
    )
    broken = list(entries)
    broken[0] = replace(broken[0], usd_value=broken[0].usd_value + Decimal("0.00000001"))
    with pytest.raises(LedgerError, match="does not balance"):
        assert_balanced(broken)
    with pytest.raises(LedgerTamperedError):
        verify_seal(broken, seal_entries(entries))


@pytest.mark.parametrize("seed", range(12))
def test_position_equals_the_sum_of_its_fills(seed: int) -> None:
    rng = random.Random(seed)
    held = Decimal(0)
    expected = Decimal(0)
    entries: list[LedgerEntry] = []
    buy_count = 0
    sell_count = 0

    for step in range(30):
        price = _price(rng)
        if held == 0 or rng.random() < 0.6:
            side = "buy"
            quantity = Decimal(rng.randint(1, 5000)) / Decimal(10 ** rng.randint(0, 4))
        else:
            side = "sell"
            quantity = (held * Decimal(str(rng.uniform(0.05, 0.6)))).quantize(
                Decimal("0.000000000001")
            )
        if quantity <= 0:
            continue
        fill = Fill(
            order_id=f"o-{seed}-{step}",
            side=side,
            quantity=quantity,
            price_usd=price,
            notional_usd=quantity * price,
            fee_usd=Decimal("0.01"),
            status="filled",
        )
        journal = journal_from_fill(fill, chain=CHAIN, token_address=TOKEN, avg_cost_usd=price)
        entries.extend(journal.entries)
        if side == "buy":
            expected += quantity
            buy_count += 1
        else:
            expected -= quantity
            sell_count += 1
        held = expected
        assert expected >= 0

    position_entries = [entry for entry in entries if entry.account == ACCOUNT]
    replayed, _cost = replay_position(position_entries)
    assert replayed == expected
    balance = next(item for item in replay_balances(entries) if item.account == ACCOUNT)
    assert balance.amount == expected
    assert buy_count > 0 and sell_count > 0
    assert sum(item.usd_minor for item in replay_balances(entries)) == 0
