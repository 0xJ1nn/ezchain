"""Indicator maths, in ``Decimal`` and nothing else.

Every function takes a sequence of closed-bar values, oldest first, and returns a list
aligned to the **end** of the input: output ``k`` describes input ``len(output) - 1``
counting back, i.e. the list is shorter by the indicator's warmup.

All arithmetic runs in a 28-digit context so a repeating fraction (an EMA multiplier, a
population stddev) is reproducible rather than float-shaped. No floats anywhere.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Context, Decimal, getcontext, localcontext

#: Working precision for every indicator. Wide enough that chained indicators stay exact
#: to 20+ decimals on realistic price magnitudes.
PRECISION = 28

ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)


def _ctx() -> Context:
    context = getcontext().copy()
    context.prec = PRECISION
    return context


def _check(period: int, name: str = "period") -> None:
    if period < 1:
        raise ValueError(f"{name} must be >= 1")


def _align(*series: Sequence[Decimal]) -> tuple[int, tuple[tuple[Decimal, ...], ...]]:
    """Tail-align inputs to the shortest one.

    Callers are supposed to pass equal-length series (all from one timeframe). Tail
    alignment keeps a defensive mismatch from raising inside a tick: it shortens the
    window instead of inventing bars.
    """
    length = min((len(item) for item in series), default=0)
    if length == 0:
        return 0, tuple(() for _ in series)
    return length, tuple(tuple(item[-length:]) for item in series)


def sma(values: Sequence[Decimal], period: int) -> list[Decimal]:
    """Simple moving average. Warmup: ``period - 1`` bars."""
    _check(period)
    with localcontext(_ctx()):
        divisor = Decimal(period)
        out: list[Decimal] = []
        for end in range(period, len(values) + 1):
            window = values[end - period : end]
            out.append(sum(window, ZERO) / divisor)
        return out


def ema(values: Sequence[Decimal], period: int) -> list[Decimal]:
    """Exponential moving average seeded with the SMA of the first ``period`` bars."""
    _check(period)
    with localcontext(_ctx()):
        multiplier = Decimal(2) / Decimal(period + 1)
        seeded = sma(values, period)
        if not seeded:
            return []
        out = [seeded[0]]
        for value in values[period:]:
            out.append(out[-1] + multiplier * (value - out[-1]))
        return out


def rsi(values: Sequence[Decimal], period: int) -> list[Decimal]:
    """Wilder RSI. Warmup: ``period`` bars (``period`` changes need ``period + 1`` closes).

    A flat window (no gains and no losses) is neutral: 50. A window with no losses but
    some gains is 100.
    """
    _check(period)
    if len(values) <= period:
        return []
    with localcontext(_ctx()):
        divisor = Decimal(period)
        gains = ZERO
        losses = ZERO
        for index in range(1, period + 1):
            change = values[index] - values[index - 1]
            if change > 0:
                gains += change
            elif change < 0:
                losses -= change
        avg_gain = gains / divisor
        avg_loss = losses / divisor

        def value(gain: Decimal, loss: Decimal) -> Decimal:
            if loss == 0:
                return HUNDRED if gain > 0 else Decimal(50)
            return HUNDRED - HUNDRED / (ONE + gain / loss)

        out = [value(avg_gain, avg_loss)]
        for index in range(period + 1, len(values)):
            change = values[index] - values[index - 1]
            gain = change if change > 0 else ZERO
            loss = -change if change < 0 else ZERO
            avg_gain = (avg_gain * (divisor - ONE) + gain) / divisor
            avg_loss = (avg_loss * (divisor - ONE) + loss) / divisor
            out.append(value(avg_gain, avg_loss))
        return out


def stddev_population(values: Sequence[Decimal]) -> Decimal:
    """Population standard deviation, ``sqrt(mean((x - mean)^2))``."""
    if not values:
        return ZERO
    with localcontext(_ctx()):
        mean = sum(values, ZERO) / Decimal(len(values))
        variance = sum(((value - mean) ** 2 for value in values), ZERO) / Decimal(len(values))
        return variance.sqrt()


def bbands(
    values: Sequence[Decimal], period: int, stddev: Decimal
) -> tuple[list[Decimal], list[Decimal], list[Decimal]]:
    """Bollinger bands as ``(upper, middle, lower)``, aligned to the end.

    Warmup: ``period - 1`` bars. The standard deviation is the *population* one.
    """
    _check(period)
    upper: list[Decimal] = []
    middle: list[Decimal] = []
    lower: list[Decimal] = []
    with localcontext(_ctx()):
        for end in range(period, len(values) + 1):
            window = values[end - period : end]
            mean = sum(window, ZERO) / Decimal(period)
            deviation = stddev_population(window) * stddev
            middle.append(mean)
            upper.append(mean + deviation)
            lower.append(mean - deviation)
    return upper, middle, lower


def true_range(
    high: Sequence[Decimal], low: Sequence[Decimal], close: Sequence[Decimal]
) -> list[Decimal]:
    """True range, aligned to the end of the inputs (one shorter than the inputs)."""
    length, (high, low, close) = _align(high, low, close)
    if length < 2:
        return []
    with localcontext(_ctx()):
        out: list[Decimal] = []
        for index in range(1, length):
            previous_close = close[index - 1]
            out.append(
                max(
                    high[index] - low[index],
                    abs(high[index] - previous_close),
                    abs(low[index] - previous_close),
                )
            )
        return out


def atr(
    high: Sequence[Decimal], low: Sequence[Decimal], close: Sequence[Decimal], period: int
) -> list[Decimal]:
    """Wilder ATR. Warmup: ``period - 1`` bars."""
    _check(period)
    ranges = true_range(high, low, close)
    if len(ranges) < period:
        return []
    with localcontext(_ctx()):
        divisor = Decimal(period)
        first = sum(ranges[:period], ZERO) / divisor
        out = [first]
        for value in ranges[period:]:
            out.append((out[-1] * (divisor - ONE) + value) / divisor)
        return out


def macd(
    values: Sequence[Decimal], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[Decimal], list[Decimal], list[Decimal]]:
    """MACD as ``(macd, signal, hist)``, all three aligned to the end.

    The three lists are the same length: the MACD line is trimmed to the signal's warmup so
    bar ``k`` of each list is the same bar. Warmup: ``(slow - 1) + (signal - 1)`` bars.
    """
    _check(fast, "fast")
    _check(slow, "slow")
    _check(signal, "signal")
    if fast >= slow:
        raise ValueError("macd needs fast < slow")
    with localcontext(_ctx()):
        fast_series = ema(values, fast)
        slow_series = ema(values, slow)
        if not slow_series:
            return [], [], []
        width = len(slow_series)
        macd_series = [fast_series[-width + i] - slow_series[i] for i in range(width)]
        signal_series = ema(macd_series, signal)
        if not signal_series:
            return macd_series, [], []
        hist_width = len(signal_series)
        tail = macd_series[-hist_width:]
        hist = [tail[i] - signal_series[i] for i in range(hist_width)]
        return tail, signal_series, hist


def highest(values: Sequence[Decimal], period: int) -> list[Decimal]:
    """Rolling maximum. Warmup: ``period - 1`` bars."""
    _check(period)
    return [max(values[end - period : end]) for end in range(period, len(values) + 1)]


def lowest(values: Sequence[Decimal], period: int) -> list[Decimal]:
    """Rolling minimum. Warmup: ``period - 1`` bars."""
    _check(period)
    return [min(values[end - period : end]) for end in range(period, len(values) + 1)]


def roc(values: Sequence[Decimal], period: int) -> list[Decimal]:
    """Rate of change in percent. Warmup: ``period`` bars.

    A bar whose base price is zero has no defined percentage: it is skipped rather than
    reported as 0, which would be a silently wrong number.
    """
    _check(period)
    with localcontext(_ctx()):
        out: list[Decimal] = []
        for index in range(period, len(values)):
            base = values[index - period]
            if base == 0:
                continue
            out.append((values[index] / base - ONE) * HUNDRED)
        return out


def vwap(
    high: Sequence[Decimal],
    low: Sequence[Decimal],
    close: Sequence[Decimal],
    volume: Sequence[Decimal],
    period: int,
) -> list[Decimal]:
    """Rolling volume-weighted average price. Warmup: ``period - 1`` bars.

    Bars where the window's volume sums to zero have no VWAP: they are skipped.
    """
    _check(period)
    length, (high, low, close, volume) = _align(high, low, close, volume)
    with localcontext(_ctx()):
        out: list[Decimal] = []
        for end in range(period, length + 1):
            start = end - period
            notional = ZERO
            total_volume = ZERO
            for index in range(start, end):
                typical = (high[index] + low[index] + close[index]) / Decimal(3)
                notional += typical * volume[index]
                total_volume += volume[index]
            if total_volume == 0:
                continue
            out.append(notional / total_volume)
        return out


__all__ = [
    "PRECISION",
    "atr",
    "bbands",
    "ema",
    "highest",
    "lowest",
    "macd",
    "roc",
    "rsi",
    "sma",
    "stddev_population",
    "true_range",
    "vwap",
]
