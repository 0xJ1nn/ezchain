"""Ledger hardening: minor-unit rounding, zero drift, replay determinism, seals.

The ledger is the only accountant (CONTRACT I1). Every USD figure it writes is an
integer number of minor units at :data:`ezchain_core.money.MONEY_QUANTUM`; the tests
here prove the policy on the arithmetic itself, independent of any read model.

The zero-drift property walks random buy/sell sequences and checks, after *every*
operation, that the group sums to exactly zero minor units, that summing Decimals and
summing integer minor units agree to the last unit, and that replaying the same tape
twice produces the same balances and the same digest.
"""

from __future__ import annotations

import random
from decimal import Decimal, localcontext

import pytest

from ezchain_core import buy_entries, genesis_entries, sell_entries
from ezchain_core.ledger import (
    LedgerEntry,
    LedgerTamperedError,
    assert_balanced,
    cash_account,
    ledger_digest,
    replay_balances,
    replay_position,
    seal_entries,
    verify_seal,
)
from ezchain_core.money import (
    MONEY_QUANTUM,
    FloatMoneyError,
    SubQuantumError,
    bps_fee,
    from_minor_units,
    quantize_money,
    require_money,
    to_minor_units,
)

GENESIS = Decimal("10000")
CHAIN = "base"
TOKEN = "0x00000000000000000000000000000000000000a1"


def _buy(notional: Decimal, price: Decimal, fee_bps: int) -> tuple[LedgerEntry, ...]:
    return buy_entries(
        chain=CHAIN,
        token_address=TOKEN,
        quantity=notional / price,
        price_usd=price,
        fee_bps=fee_bps,
        notional_usd=notional,
    )


def _sell(
    quantity: Decimal,
    price: Decimal,
    basis: Decimal,
    fee_bps: int,
) -> tuple[LedgerEntry, ...]:
    return sell_entries(
        chain=CHAIN,
        token_address=TOKEN,
        quantity=quantity,
        price_usd=price,
        avg_cost_usd=basis / quantity,
        fee_bps=fee_bps,
        proceeds_usd=quantize_money(quantity * price),
        cost_basis_usd=basis,
    )


def test_rounding_policy_is_one_minor_unit_of_exact_usd() -> None:
    assert MONEY_QUANTUM == Decimal("0.00000001")
    assert to_minor_units(Decimal("1")) == 100_000_000
    assert from_minor_units(100_000_000) == Decimal("1")
    assert to_minor_units(from_minor_units(123_456_789)) == 123_456_789


def test_rounding_policy_is_half_even_at_the_ledger_boundary() -> None:
    # Exactly halfway between 0 and 1e-8: the even neighbour wins.
    assert quantize_money(Decimal("0.000000005")) == Decimal("0.00000000")
    # Exactly halfway between 1e-8 and 2e-8: 2e-8 is the even neighbour.
    assert quantize_money(Decimal("0.000000015")) == Decimal("0.00000002")
    # A value already on the quantum is unchanged.
    assert quantize_money(Decimal("0.00000001")) == Decimal("0.00000001")
    assert quantize_money(Decimal("-0.000000015")) == Decimal("-0.00000002")


def test_minor_units_reject_floats_at_every_boundary() -> None:
    """A float never crosses the ledger boundary, not even as ``Decimal(str(x))``."""
    with pytest.raises(FloatMoneyError):
        quantize_money(0.1)
    with pytest.raises(FloatMoneyError):
        require_money(0.1)
    with pytest.raises(FloatMoneyError):
        to_minor_units(0.1)
    with pytest.raises(FloatMoneyError):
        bps_fee(10.0, 30)  # type: ignore[arg-type]
    with pytest.raises(FloatMoneyError):
        LedgerEntry(cash_account(), "USD", Decimal("1"), 0.1, "buy")  # type: ignore[arg-type]


def test_minor_units_reject_non_finite_values() -> None:
    with pytest.raises(ValueError):
        quantize_money(Decimal("NaN"))
    with pytest.raises(ValueError):
        quantize_money(Decimal("Infinity"))


def test_sub_minor_values_fail_closed() -> None:
    with pytest.raises(SubQuantumError):
        require_money(Decimal("1.000000001"))
    with pytest.raises(SubQuantumError):
        LedgerEntry(cash_account(), "USD", Decimal("1"), Decimal("1.000000001"), "buy")


def test_assert_balanced_is_exact_to_the_minor_unit() -> None:
    entries = (
        LedgerEntry(cash_account(), "USD", Decimal("1"), Decimal("1"), "buy"),
        LedgerEntry("position:x:y", "Y", Decimal("1"), Decimal("-1.00000001"), "buy"),
    )
    with pytest.raises(ValueError):
        assert_balanced(entries)
    assert_balanced(
        (
            LedgerEntry(cash_account(), "USD", Decimal("1"), Decimal("1"), "buy"),
            LedgerEntry("position:x:y", "Y", Decimal("1"), Decimal("-1"), "buy"),
        )
    )


def test_replay_balances_is_canonical_and_deterministic() -> None:
    tape = list(genesis_entries(GENESIS)) + list(_buy(Decimal("200"), Decimal("2000"), 30))
    first = replay_balances(tape)
    second = replay_balances(list(tape))
    assert first == second
    by_account = {row.account: row for row in first}
    assert by_account[cash_account()].usd_minor == to_minor_units(Decimal("9799.4"))
    assert by_account[cash_account()].amount == Decimal("9799.4")


def test_immutability_seal_is_stable_and_detects_a_changed_entry() -> None:
    tape = list(genesis_entries(GENESIS)) + list(_buy(Decimal("200"), Decimal("2000"), 30))
    seal = seal_entries(tape)
    assert seal.count == len(tape)
    assert seal.head == ledger_digest(tape)
    assert seal_entries(list(tape)) == seal
    verify_seal(tape, seal)

    tampered = list(tape)
    original = tampered[-1]
    tampered[-1] = LedgerEntry(
        original.account,
        original.asset,
        original.amount + Decimal("1"),
        original.usd_value,
        original.kind,
    )
    assert ledger_digest(tampered) != seal.head
    with pytest.raises(LedgerTamperedError):
        verify_seal(tampered, seal)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6, 7, 8])
def test_zero_drift_across_random_buy_sell_sequences(seed: int) -> None:
    """Every operation balances to exactly zero minor units, with no drift anywhere."""
    rng = random.Random(seed)
    tape: list[LedgerEntry] = list(genesis_entries(GENESIS))
    price = Decimal("2")
    quantity = Decimal(0)
    cost_minor = 0
    cash_minor = to_minor_units(GENESIS)

    for step in range(250):
        # A price walk in minor units keeps the generator itself exact.
        price = max(
            MONEY_QUANTUM,
            price + from_minor_units(rng.randrange(-10_000_000, 10_000_000)),
        )
        step_rng = random.Random(f"{seed}:{step}")
        notional = from_minor_units(step_rng.randrange(100, 50_000_000))
        fee_bps = step_rng.choice([0, 10, 30, 50])

        if quantity <= 0 or step_rng.random() < 0.6:
            entries = _buy(notional, price, fee_bps)
            group_fee_minor = to_minor_units(bps_fee(notional, fee_bps))
            quantity += notional / price
            cost_minor += to_minor_units(notional)
            cash_minor -= to_minor_units(notional) + group_fee_minor
        else:
            sold = min(quantity, quantity * Decimal(step_rng.randrange(1, 100)) / 100)
            if sold <= 0:
                continue
            proceeds = quantize_money(sold * price)
            held_minor = cost_minor
            if sold == quantity:
                basis = from_minor_units(held_minor)
            else:
                basis = min(
                    quantize_money(from_minor_units(held_minor) * sold / quantity),
                    from_minor_units(held_minor),
                )
            entries = _sell(sold, price, basis, fee_bps)
            group_fee_minor = to_minor_units(bps_fee(proceeds, fee_bps))
            quantity -= sold
            cost_minor -= to_minor_units(basis)
            cash_minor += to_minor_units(proceeds) - group_fee_minor

        # The factory invariant: the group is exactly balanced in integer minor units.
        assert sum(to_minor_units(entry.usd_value) for entry in entries) == 0
        # Summing Decimals and summing integer minor units are the same number.
        assert to_minor_units(sum((e.usd_value for e in entries), Decimal(0))) == 0
        # Every written value is a whole number of minor units.
        assert all(require_money(entry.usd_value) == entry.usd_value for entry in entries)

        tape.extend(entries)

        # Replay is deterministic: same tape, same balances, same evidence.
        balances = replay_balances(tape)
        assert replay_balances(list(tape)) == balances
        if step % 25 == 0:
            assert seal_entries(tape) == seal_entries(list(tape))

        # The total cash account equals the integer accumulator, to the minor unit.
        cash = sum(row.usd_minor for row in balances if row.account == cash_account())
        assert cash == cash_minor, f"cash drift at seed {seed} step {step}"
        # The position cost basis equals the integer accumulator.
        position_rows = [row for row in balances if row.account.startswith("position:")]
        assert sum(row.usd_minor for row in position_rows) == cost_minor
        replayed_quantity, replayed_cost = replay_position(
            entry for entry in tape if entry.account.startswith("position:")
        )
        with localcontext() as context:
            context.prec = 80
            exact_quantity = sum(
                (entry.amount for entry in tape if entry.account.startswith("position:")),
                Decimal(0),
            )
        assert replayed_quantity == exact_quantity
        assert to_minor_units(replayed_cost) == cost_minor

    # A long tape leaves no residual at all: the whole ledger sums to zero.
    assert sum(to_minor_units(entry.usd_value) for entry in tape) == 0
    assert len(tape) > 500
