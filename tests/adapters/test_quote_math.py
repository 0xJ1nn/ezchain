"""Property-style checks for the quote maths (parametrised tables and a seeded loop).

``hypothesis`` is not installed, so the invariants are exercised with a parametrised table
plus a seeded random sweep. What is asserted is the property, not an example:

* scaling to raw units truncates and never invents value, and unscaling is monotone;
* a buy price is USD-per-token from what was paid, a sell price from what was received;
* the derived impact is antisymmetric under swapping the two USD legs around parity.
"""

from __future__ import annotations

import random
from decimal import Decimal

import pytest

from ezchain_adapters.quotes import from_raw, quote_price, to_raw, value_impact

SCALES = [
    (0, "1"),
    (6, "1"),
    (6, "0.000001"),
    (8, "12.34567891"),
    (9, "1"),
    (18, "0.000000000000000001"),
]
DECIMALS = [0, 2, 6, 8, 9, 18]


@pytest.mark.parametrize(("decimals", "amount"), SCALES)
def test_scaling_round_trips_within_one_unit(decimals: int, amount: str) -> None:
    value = Decimal(amount)
    raw = to_raw(value, decimals)
    back = from_raw(raw, decimals)
    assert raw >= 0
    # truncation never rounds up, and never loses more than one raw unit
    assert back <= value
    assert value - back < Decimal(1) / (Decimal(10) ** decimals)


@pytest.mark.parametrize("decimals", DECIMALS)
def test_naive_scaling_tables_agree_with_the_decimal_implementation(decimals: int) -> None:
    for thousandths in (1, 7, 999, 123456):
        value = Decimal(thousandths) / Decimal(1000)
        expected = int(value * (Decimal(10) ** decimals))
        assert to_raw(value, decimals) == expected
        assert from_raw(expected, decimals) == Decimal(expected) / (Decimal(10) ** decimals)


@pytest.mark.parametrize("decimals", [-1, 37])
def test_out_of_range_decimals_fail_closed(decimals: int) -> None:
    from ezchain_adapters import NotFound

    with pytest.raises(NotFound):
        to_raw(Decimal(1), decimals)


def test_buy_price_is_usd_paid_per_token() -> None:
    price = quote_price(
        side="buy",
        amount_in=Decimal("100"),
        amount_out=Decimal("0.04"),
        amount_in_usd=Decimal("99.5"),
        amount_out_usd=Decimal("100.2"),
    )
    assert price == Decimal("99.5") / Decimal("0.04")


def test_sell_price_is_usd_received_per_token() -> None:
    price = quote_price(
        side="sell",
        amount_in=Decimal("0.04"),
        amount_out=Decimal("100"),
        amount_in_usd=Decimal("99.5"),
        amount_out_usd=Decimal("99.9"),
    )
    assert price == Decimal("99.9") / Decimal("0.04")


def test_price_falls_back_to_the_token_legs_without_usd_prices() -> None:
    assert quote_price(side="buy", amount_in=Decimal(2), amount_out=Decimal(4)) == Decimal("0.5")
    assert quote_price(side="sell", amount_in=Decimal(4), amount_out=Decimal(2)) == Decimal("0.5")


def test_impact_is_zero_when_both_legs_agree() -> None:
    assert value_impact(amount_in_usd=Decimal("100"), amount_out_usd=Decimal("100")) == 0


def test_impact_is_signed_and_antisymmetric() -> None:
    loss = value_impact(amount_in_usd=Decimal("100"), amount_out_usd=Decimal("99"))
    gain = value_impact(amount_in_usd=Decimal("100"), amount_out_usd=Decimal("101"))
    assert loss == Decimal("-0.01")
    assert gain == Decimal("0.01")
    assert loss == -gain


@pytest.mark.parametrize(
    ("amount_in_usd", "amount_out_usd"),
    [(None, Decimal(1)), (Decimal(1), None), (Decimal(0), Decimal(1)), (None, None)],
)
def test_impact_is_unknown_without_both_legs(
    amount_in_usd: Decimal | None, amount_out_usd: Decimal | None
) -> None:
    assert value_impact(amount_in_usd=amount_in_usd, amount_out_usd=amount_out_usd) is None


def test_seeded_sweep_keeps_scaling_and_price_monotone() -> None:
    rng = random.Random(20261022)
    for _ in range(200):
        decimals = rng.choice(DECIMALS)
        tokens = Decimal(rng.randint(1, 10**9)) / Decimal(10**decimals)
        raw = to_raw(tokens, decimals)
        assert raw == int(tokens * (Decimal(10) ** decimals))
        assert from_raw(raw, decimals) <= tokens

        price = quote_price(side="buy", amount_in=Decimal("100"), amount_out=tokens)
        assert price > 0
        # twice the tokens for the same USD can only mean a lower price per token
        bigger = quote_price(side="buy", amount_in=Decimal("100"), amount_out=tokens * 2)
        assert bigger < price
