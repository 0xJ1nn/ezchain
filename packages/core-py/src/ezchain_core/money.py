"""Money helpers: cents at the edge, Decimal inside."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

CENTS = Decimal(100)
BPS = Decimal(10_000)


def cents_to_usd(cents: int) -> Decimal:
    return (Decimal(cents) / CENTS).quantize(Decimal("0.01"))


def usd_to_cents(value: Decimal) -> int:
    return int((value * CENTS).to_integral_value(rounding=ROUND_DOWN))


def bps_fee(notional_usd: Decimal, fee_bps: int) -> Decimal:
    if fee_bps < 0:
        raise ValueError("fee_bps cannot be negative")
    return (notional_usd * Decimal(fee_bps) / BPS).quantize(Decimal("0.000001"))
