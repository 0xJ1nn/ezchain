"""Indicator maths against literal reference tables.

Every expected value below was computed **independently** of this package, with exact
rational (``fractions.Fraction``) arithmetic on the same input table, and pasted here as a
literal. That keeps the test an oracle rather than a copy of the implementation.

Alignment rule under test: an indicator's output is a suffix of the input's own index
space — ``len(out) == len(input) - warmup``, ``out[-1]`` is the last closed bar.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from ezchain_core.graph.indicators import (
    atr,
    bbands,
    ema,
    highest,
    lowest,
    macd,
    roc,
    rsi,
    sma,
    stddev_population,
    true_range,
    vwap,
)


def decimals(*values: str) -> list[Decimal]:
    return [Decimal(value) for value in values]


def assert_series(actual: list[Decimal], expected: list[str], *, places: str = "1e-18") -> None:
    tolerance = Decimal(places)
    assert len(actual) == len(expected), f"length {len(actual)} != {len(expected)}"
    for index, (got, want) in enumerate(zip(actual, expected, strict=True)):
        assert isinstance(got, Decimal), f"index {index} is {type(got).__name__}, not Decimal"
        assert abs(got - Decimal(want)) <= tolerance, f"index {index}: {got} != {want}"


def test_no_floats_anywhere() -> None:
    series = decimals("1", "2", "3", "4", "5")
    for output in (
        sma(series, 2),
        ema(series, 2),
        rsi(series, 2),
        highest(series, 2),
        lowest(series, 2),
        roc(series, 2),
    ):
        assert all(isinstance(value, Decimal) for value in output)
    upper, middle, lower = bbands(series, 2, Decimal("2"))
    assert all(isinstance(value, Decimal) for value in (*upper, *middle, *lower))


# -- SMA / EMA -------------------------------------------------------------------------


def test_sma_literal_table() -> None:
    assert_series(sma(decimals("1", "2", "3", "4", "5"), 3), ["2", "3", "4"])


def test_sma_warmup_is_period_minus_one() -> None:
    series = decimals("10", "20", "30", "40")
    assert len(sma(series, 1)) == 4
    assert len(sma(series, 2)) == 3
    assert len(sma(series, 3)) == 2
    assert sma(series, 5) == []


def test_ema_is_seeded_with_the_sma() -> None:
    series = decimals("1", "2", "3", "4", "5")
    output = ema(series, 3)
    # Seed = SMA(1,2,3) = 2; k = 2/(3+1) = 0.5 -> 2, 3, 4.
    assert_series(output, ["2", "3", "4"])
    assert output[0] == sma(series, 3)[0]


def test_ema_literal_table() -> None:
    series = decimals("1", "2", "4", "8", "16")
    # k = 2/3, seed = 1.5
    assert_series(
        ema(series, 2),
        [
            "1.5",
            "3.166666666666666666666666667",
            "6.388888888888888888888888889",
            "12.79629629629629629629629630",
        ],
        places="1e-24",
    )


# -- RSI (Wilder) ----------------------------------------------------------------------


#: Wilder's classic 20-close example. Expected RSI(14) values were produced by exact
#: rational arithmetic over these closes (see the module docstring).
WILDER_CLOSES = decimals(
    "44.34", "44.09", "44.15", "43.61", "44.33", "44.83", "45.10", "45.42", "45.84",
    "46.08", "45.89", "46.03", "45.61", "46.28", "46.28", "46.00", "46.03", "46.41",
    "46.22", "45.64",
)  # fmt: skip


def test_rsi_wilder_literal_table() -> None:
    output = rsi(WILDER_CLOSES, 14)
    assert_series(
        output,
        ["70.464135021", "66.249618554", "66.480941835", "69.346853163",
         "66.294712659", "57.915020670"],
        places="1e-8",
    )  # fmt: skip


def test_rsi_warmup_and_edges() -> None:
    assert rsi(decimals("1", "2", "3"), 14) == []
    # Monotonic rise: no losses at all -> 100, never a division by zero.
    assert rsi(decimals("1", "2", "3", "4", "5"), 3) == [Decimal(100), Decimal(100)]
    # Perfectly flat: no gains and no losses -> neutral 50 (documented choice).
    assert rsi(decimals("5", "5", "5", "5", "5"), 3) == [Decimal(50), Decimal(50)]
    # Monotonic fall: no gains -> 0.
    assert rsi(decimals("5", "4", "3", "2", "1"), 3) == [Decimal(0), Decimal(0)]


# -- Bollinger bands -------------------------------------------------------------------


def test_stddev_population_literal() -> None:
    # mean 2, squared deviations 1,0,1 -> variance 2/3 -> sqrt(2/3)
    assert abs(
        stddev_population(decimals("1", "2", "3")) - Decimal("0.8164965809277260327324280249")
    ) < Decimal("1e-27")
    assert stddev_population([]) == Decimal(0)


def test_bbands_literal_table() -> None:
    upper, middle, lower = bbands(decimals("1", "2", "3", "4", "5"), 3, Decimal("2"))
    assert_series(middle, ["2", "3", "4"])
    assert_series(
        upper,
        [
            "3.632993161855452065464856049803927594644",
            "4.632993161855452065464856049803927594644",
            "5.632993161855452065464856049803927594644",
        ],
        places="1e-24",
    )
    assert_series(
        lower,
        [
            "0.367006838144547934535143950196072405356",
            "1.367006838144547934535143950196072405356",
            "2.367006838144547934535143950196072405356",
        ],
        places="1e-24",
    )


def test_bbands_with_zero_width_is_the_middle_band() -> None:
    upper, middle, lower = bbands(decimals("1", "2", "3"), 3, Decimal("0"))
    assert upper == middle == lower


# -- True range and ATR ----------------------------------------------------------------


def test_true_range_literal_table() -> None:
    high = decimals("10", "11", "12", "11", "13")
    low = decimals("9", "9", "10", "9", "11")
    close = decimals("9.5", "10.5", "11", "9.5", "12.5")
    assert_series(true_range(high, low, close), ["2", "2", "2", "3.5"])


def test_atr_wilder_literal_table() -> None:
    high = decimals("10", "11", "12", "11", "13")
    low = decimals("9", "9", "10", "9", "11")
    close = decimals("9.5", "10.5", "11", "9.5", "12.5")
    # TRs are 2,2,2,3.5; seed = 2; then (2*2 + 3.5)/3 = 2.5
    assert_series(atr(high, low, close, 3), ["2", "2.5"])


def test_atr_needs_enough_bars() -> None:
    assert atr(decimals("1"), decimals("1"), decimals("1"), 3) == []
    assert atr(decimals("1", "2"), decimals("1", "1"), decimals("1", "2"), 3) == []


# -- MACD ------------------------------------------------------------------------------


def test_macd_literal_table() -> None:
    series = decimals("1", "2", "4", "8", "16")
    macd_series, signal_series, hist = macd(series, 2, 3, 2)
    # All three series are aligned to the end and equal length: the MACD line is trimmed to
    # the signal's warmup so bar k of each list is bar k of the others.
    assert_series(
        macd_series,
        ["1.222222222222222222222222222", "2.212962962962962962962962963"],
        places="1e-24",
    )
    assert_series(
        signal_series,
        ["1.027777777777777777777777778", "1.817901234567901234567901235"],
        places="1e-24",
    )
    assert_series(
        hist,
        ["0.1944444444444444444444444444", "0.3950617283950617283950617284"],
        places="1e-24",
    )
    # Every returned list is aligned to the end of the input.
    assert len(macd_series) == len(signal_series) == len(hist)


def test_macd_rejects_fast_slow_order() -> None:
    with pytest.raises(ValueError):
        macd(decimals("1", "2", "3"), 5, 3, 2)


def test_macd_empty_below_warmup() -> None:
    # One MACD value, but not yet enough of them for a signal line.
    assert macd(decimals("1", "2", "3"), 2, 3, 2) == (
        [Decimal("0.500000000000000000000000000")],
        [],
        [],
    )
    assert macd(decimals("1", "2"), 2, 3, 2) == ([], [], [])


# -- highest / lowest / roc ------------------------------------------------------------


def test_highest_and_lowest_literal_tables() -> None:
    series = decimals("1", "5", "2", "4")
    assert_series(highest(series, 2), ["5", "5", "4"])
    assert_series(lowest(series, 2), ["1", "2", "2"])
    assert_series(highest(series, 1), ["1", "5", "2", "4"])
    assert highest(series, 9) == []


def test_roc_literal_tables() -> None:
    series = decimals("1", "2", "4")
    assert_series(roc(series, 1), ["100", "100"])
    assert_series(roc(series, 2), ["300"])


def test_roc_skips_a_zero_base() -> None:
    # A zero base has no defined percentage: the bar is skipped, never reported as 0.
    assert_series(roc(decimals("0", "2", "4"), 1), ["100"])


# -- VWAP ------------------------------------------------------------------------------


def test_vwap_literal_table() -> None:
    high = decimals("10", "11")
    low = decimals("8", "9")
    close = decimals("9", "10")
    volume = decimals("1", "3")
    # tp1 = 9, tp2 = 10 -> (9*1 + 10*3) / 4 = 9.75
    assert_series(vwap(high, low, close, volume, 2), ["9.75"])


def test_vwap_skips_a_zero_volume_window() -> None:
    high = decimals("10", "11")
    low = decimals("8", "9")
    close = decimals("9", "10")
    assert vwap(high, low, close, decimals("0", "0"), 2) == []


def test_mismatched_lengths_are_tail_aligned_not_raised() -> None:
    """Defensive: a shorter series shortens the window instead of inventing bars."""
    high = decimals("10", "11", "12")
    low = decimals("9", "10")
    close = decimals("9.5", "10.5", "11.5")
    ranges = true_range(high, low, close)
    assert len(ranges) == 1  # aligned to the shortest input (2 bars -> 1 range)


@pytest.mark.parametrize("period", [0, -1])
def test_periods_must_be_positive(period: int) -> None:
    with pytest.raises(ValueError):
        sma(decimals("1", "2"), period)
