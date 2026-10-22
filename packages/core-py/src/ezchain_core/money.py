"""Money helpers: exact Decimal arithmetic; no floats."""

from __future__ import annotations

from decimal import Decimal

BPS = Decimal(10_000)


def bps_fee(notional_usd: Decimal, fee_bps: int) -> Decimal:
    if fee_bps < 0:
        raise ValueError("fee_bps cannot be negative")
    return (notional_usd * Decimal(fee_bps) / BPS).quantize(Decimal("0.000001"))
