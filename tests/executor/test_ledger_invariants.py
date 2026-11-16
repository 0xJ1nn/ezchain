"""Property checks for ledger identity and exact fee accounting.

``hypothesis`` is not a dependency of this repository, so the corpus is a seeded random
sweep plus explicit hard cases, in the same style as the quote-math property tests. Every
case is a venue receipt at the journal-from-receipt seam: buys, sells, partial fills,
reduce-only exits and repeating-decimal notionals. The invariants are the ones that matter
when money moves:

* every journal balances at zero minor units, with no tolerance;
* replaying the appended entries gives the same per-account figures as summing them;
* fee legs are exact integer minor units and net to zero;
* the booked cash figure is the figure the receipt moved, quantized once at the boundary;
* the same receipt always journals to the same entries, and a doubled tape doubles the
  replay rather than drifting.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from decimal import Decimal

from ezchain_core import (
    Fill,
    assert_balanced,
    cash_account,
    exact_product,
    fee_account,
    journal_from_fill,
    quantize_money,
    replay_balances,
    to_minor_units,
)

CHAIN = "solana"
TOKEN = "Mint1111111111111111111111111111111111111111"
SEED = 20261116


def _fill(
    *,
    order: str,
    side: str,
    quantity: Decimal,
    price: Decimal,
    fee: Decimal,
    partial: bool,
) -> Fill:
    return Fill(
        order_id=order,
        side=side,  # type: ignore[arg-type]
        quantity=quantity,
        price_usd=price,
        notional_usd=quantity * price,
        fee_usd=fee,
        status="partial" if partial else "filled",
        receipt={"venue": "generated"},
    )


def corpus() -> Iterator[tuple[Fill, Decimal]]:
    """A deterministic corpus, including the hard repeating-decimal cases."""
    rng = random.Random(SEED)
    hard = (
        (Decimal(1) / Decimal(3), Decimal("7.99")),
        (Decimal(100) / Decimal(7), Decimal("0.37")),
        (Decimal(1) / Decimal(9), Decimal("300")),
        (Decimal("12.5"), Decimal(2) / Decimal(3)),
    )
    index = 0
    for price, fee in hard:
        for side in ("buy", "sell"):
            for partial in (False, True):
                index += 1
                yield (
                    _fill(
                        order=f"order:{index}",
                        side=side,
                        quantity=Decimal("3") / Decimal(7) if partial else Decimal("13"),
                        price=price,
                        fee=fee,
                        partial=partial,
                    ),
                    Decimal("2"),
                )

    for _ in range(400):
        index += 1
        side = "buy" if rng.randrange(2) else "sell"
        partial = bool(rng.randrange(2))
        # Integer minor units by construction for the fee; repeating decimals for the
        # notional come from the price and from a non-terminating quantity.
        fee = Decimal(rng.randrange(0, 999)) / Decimal(100)
        price = Decimal(rng.randrange(1, 100000)) / Decimal(100)
        quantity = (
            Decimal(rng.randrange(1, 100000)) / Decimal(7)
            if partial
            else Decimal(rng.randrange(1, 100000)) / Decimal(100)
        )
        yield (
            _fill(
                order=f"order:{index}",
                side=side,
                quantity=quantity,
                price=price,
                fee=fee,
                partial=partial,
            ),
            Decimal(rng.randrange(1, 100000)) / Decimal(100),
        )


def _journal(fill: Fill, avg_cost: Decimal):
    # A reduce-only exit is a sell whose cost basis is bounded by the whole position;
    # the receipt decides how much of that basis this fill removes.
    basis = None
    if fill.side == "sell":
        basis = min(Decimal("10000"), quantize_money(exact_product(fill.quantity, avg_cost)))
    return journal_from_fill(
        fill,
        chain=CHAIN,
        token_address=TOKEN,
        avg_cost_usd=avg_cost,
        cost_basis_usd=basis,
    )


def test_every_generated_journal_balances_at_zero_minor_units() -> None:
    count = 0
    for fill, avg_cost in corpus():
        journal = _journal(fill, avg_cost)
        assert_balanced(journal.entries)
        assert sum(to_minor_units(entry.usd_value) for entry in journal.entries) == 0
        assert journal.group_id == f"fill:{fill.order_id}"
        count += 1
    assert count == 416, "the seeded corpus must be the one the other properties check"


def test_replay_balances_equals_the_sum_of_the_appended_entries() -> None:
    for fill, avg_cost in corpus():
        entries = _journal(fill, avg_cost).entries
        by_account: dict[str, int] = {}
        for entry in entries:
            by_account[entry.account] = by_account.get(entry.account, 0) + to_minor_units(
                entry.usd_value
            )
        replayed = {row.account: row.usd_minor for row in replay_balances(entries)}
        assert replayed == by_account


def test_fee_accounting_is_exact_in_integer_minor_units() -> None:
    for fill, avg_cost in corpus():
        entries = _journal(fill, avg_cost).entries
        fee = to_minor_units(quantize_money(fill.fee_usd))
        legs = [entry for entry in entries if entry.kind == "fee"]
        cash = next(entry for entry in legs if entry.account == cash_account())
        booked = next(entry for entry in legs if entry.account == fee_account())
        assert to_minor_units(cash.usd_value) == -fee
        assert to_minor_units(booked.usd_value) == fee
        assert sum(to_minor_units(entry.usd_value) for entry in legs) == 0
        assert fee >= 0, "a receipt cannot book a negative fee"


def test_no_rounding_path_books_a_different_cash_figure_than_the_receipt_moved() -> None:
    for fill, avg_cost in corpus():
        entries = _journal(fill, avg_cost).entries
        notional = to_minor_units(quantize_money(fill.notional_usd))
        fee = to_minor_units(quantize_money(fill.fee_usd))
        cash = sum(
            to_minor_units(entry.usd_value) for entry in entries if entry.account == cash_account()
        )
        if fill.side == "buy":
            assert cash == -notional - fee
        else:
            assert cash == notional - fee


def test_the_same_receipt_journals_identically_and_replay_is_linear() -> None:
    for fill, avg_cost in corpus():
        first = _journal(fill, avg_cost)
        second = _journal(fill, avg_cost)
        assert first.entries == second.entries
        once = replay_balances(first.entries)
        twice = replay_balances(first.entries + second.entries)
        assert {row.account: row.usd_minor for row in twice} == {
            row.account: 2 * row.usd_minor for row in once
        }
