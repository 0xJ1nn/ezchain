"""A venue that settles in memory: the one the acceptance tests drive.

It speaks only the :class:`~ezchain_core.execution.Venue` protocol, so the pipeline
cannot tell it from a real adapter. It never holds funds or keys; a "fill" is a receipt
derived from its configured price and fee, which is exactly the shape a live adapter
returns after confirmation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from ezchain_core import Quote, SourceRef, utcnow
from ezchain_core.execution import Fill, Intent, Order

VenueMode = Literal["fill", "reject", "timeout", "partial", "quote_timeout", "no_quote"]


class VenueError(RuntimeError):
    """A venue refused the order. Carries the code the pipeline records."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


@dataclass
class FakeVenue:
    """A deterministic venue for the pipeline's contract tests."""

    price_usd: Decimal = Decimal("2")
    fee_usd: Decimal = Decimal("0")
    mode: VenueMode = "fill"
    partial_ratio: Decimal = Decimal("0.25")
    quote_observed_at: datetime | None = None
    timeout_seconds: float = 10.0
    quote_calls: int = 0
    submit_calls: int = 0
    submitted: list[Order] = field(default_factory=list)

    def _source(self) -> SourceRef:
        return SourceRef(
            name="fake",
            url=None,
            observed_at=self.quote_observed_at or utcnow(),
        )

    async def quote(self, intent: Intent) -> Quote:
        self.quote_calls += 1
        if self.mode == "no_quote":
            raise VenueError("NO_QUOTE")
        if self.mode == "quote_timeout":
            await asyncio.sleep(self.timeout_seconds)
        if intent.side == "buy":
            # A buy is sized in USD: the venue says how many tokens that buys.
            token_in, token_out = "USD", intent.token_address
            amount_in = intent.amount_usd
            amount_out = intent.amount_usd / self.price_usd
        else:
            # A sell is sized in tokens: the venue says how much USD they fetch.
            token_in, token_out = intent.token_address, "USD"
            amount_in = intent.quantity or Decimal(0)
            amount_out = amount_in * self.price_usd
        return Quote(
            chain=intent.chain,
            token_in=token_in,
            token_out=token_out,
            side=intent.side,
            amount_in=amount_in,
            amount_out=amount_out,
            price=self.price_usd,
            price_impact_pct=Decimal("0"),
            fee_usd=self.fee_usd,
            kind="paper_mark",
            route="fake",
            source=self._source(),
        )

    async def submit(self, order: Order) -> Fill:
        self.submit_calls += 1
        self.submitted.append(order)
        if self.mode == "reject":
            raise VenueError("VENUE_REJECTED")
        if self.mode == "timeout":
            await asyncio.sleep(self.timeout_seconds)
        receipt: Mapping[str, Any] = {"venue": "fake", "order": order.id}
        if self.mode == "partial":
            quantity = order.quantity * self.partial_ratio
            return Fill(
                order_id=order.id,
                side=order.side,
                quantity=quantity,
                price_usd=order.quote.price,
                notional_usd=quantity * order.quote.price,
                fee_usd=self.fee_usd * self.partial_ratio,
                status="partial",
                receipt=receipt,
            )
        return Fill(
            order_id=order.id,
            side=order.side,
            quantity=order.quantity,
            price_usd=order.quote.price,
            notional_usd=order.amount_usd,
            fee_usd=self.fee_usd,
            status="filled",
            receipt=receipt,
        )


__all__ = ["FakeVenue", "VenueError", "VenueMode"]
