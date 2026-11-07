"""Money helpers: exact Decimal arithmetic; no floats.

The pre-trade cost preview lives here too, not at the edge: the web preview and the
journal must agree on one cost model (CONTRACT I1/I7), so ``preview_cost`` produces
the exact fill fee the ledger journals for the same quote. The web fixture mirrors
this module; it never invents a second fee formula.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .types import SourceRef

BPS = Decimal(10_000)
# The slippage window every executable quote is requested with. Quotes are requested
# at this window (adapters default to it), so the preview can quote the receiver's
# worst case inside the window without another provider round-trip.
DEFAULT_SLIPPAGE_BPS = 50
# The non-executable DexScreener mark kind. Mirrors the adapters' PAPER_MARK literal;
# core imports nothing from the adapters, so the value is duplicated here by necessity.
PAPER_MARK = "paper_mark"


def bps_fee(notional_usd: Decimal, fee_bps: int) -> Decimal:
    if fee_bps < 0:
        raise ValueError("fee_bps cannot be negative")
    return (notional_usd * Decimal(fee_bps) / BPS).quantize(Decimal("0.000001"))


@dataclass(frozen=True)
class CostPreview:
    """The full pre-trade cost sheet for one quote, as the journal will see it.

    ``platform_fee_usd`` is always zero (self-hosted: the software takes no cut).
    ``venue_fee_usd`` is the quote's own provider cost. ``network_fee_usd`` is null:
    no adapter breaks gas or priority cost out in USD, so the sheet prints an em
    dash — an unknown, never a zero. ``worst_case_out`` is the receiver's minimum
    amount inside the slippage window; it is null for a paper mark, which has no
    venue to guarantee any window. ``fill_fee_usd`` is the fee the ledger journals.
    """

    platform_fee_usd: Decimal
    venue_fee_usd: Decimal
    network_fee_usd: Decimal | None
    price_impact_pct: Decimal | None
    slippage_bps: int | None
    worst_case_out: Decimal | None
    fill_fee_usd: Decimal
    source: SourceRef

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform_fee_usd": str(self.platform_fee_usd),
            "venue_fee_usd": str(self.venue_fee_usd),
            "network_fee_usd": None if self.network_fee_usd is None else str(self.network_fee_usd),
            "price_impact_pct": None
            if self.price_impact_pct is None
            else str(self.price_impact_pct),
            "slippage_bps": self.slippage_bps,
            "worst_case_out": None if self.worst_case_out is None else str(self.worst_case_out),
            "fill_fee_usd": str(self.fill_fee_usd),
            "source": self.source.to_dict(),
        }


def preview_cost(
    *,
    kind: str,
    notional_usd: Decimal,
    venue_fee_usd: Decimal,
    price_impact_pct: Decimal | None,
    amount_out: Decimal | None,
    fee_bps: int,
    source: SourceRef,
) -> CostPreview:
    """Compute the cost sheet for one quote. Pure: no I/O, no settings.

    ``fill_fee_usd`` is the journal rule: a paper mark pays the flat bps fee; an
    executable quote pays the larger of the bps leg and its own venue cost, never
    their sum (the venue already prices pool fees and gas). ``worst_case_out`` is
    the expected out less the slippage window, rounded like every other money
    figure; it is null when there is no window to guarantee.
    """
    if notional_usd <= 0:
        raise ValueError("notional must be positive")
    paper_leg = bps_fee(notional_usd, fee_bps)
    fill_fee = paper_leg if kind == PAPER_MARK else max(paper_leg, venue_fee_usd)
    slippage_bps = None if kind == PAPER_MARK else DEFAULT_SLIPPAGE_BPS
    worst_case_out: Decimal | None = None
    if slippage_bps is not None and amount_out is not None:
        worst_case_out = (amount_out * (Decimal(1) - Decimal(slippage_bps) / BPS)).quantize(
            Decimal("0.000001")
        )
    return CostPreview(
        platform_fee_usd=Decimal(0),
        venue_fee_usd=venue_fee_usd,
        network_fee_usd=None,
        price_impact_pct=price_impact_pct,
        slippage_bps=slippage_bps,
        worst_case_out=worst_case_out,
        fill_fee_usd=fill_fee,
        source=source,
    )
