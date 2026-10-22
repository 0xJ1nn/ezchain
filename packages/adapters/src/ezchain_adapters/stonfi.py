"""STON.fi ``/v1/swap/simulate`` quote adapter (TON, keyless).

Verified live 2026-10-22. Two protocol quirks:

* It is a **POST whose arguments go in the query string**. A JSON body is rejected with
  ``Failed to deserialize query string: missing field 'offer_address'``.
* The response publishes ``price_impact`` as a ratio (``0.000000404`` is 0.00004%) and
  ``min_ask_units`` at the requested slippage, but no USD gas price: ``gas_params`` is in
  nanoTON. ``fee_usd`` therefore stays zero instead of inventing a TON price.

TON has no USDC pool worth quoting, so the chain's stable is the USDT jetton
(``ChainMeta.quote_asset``). Native TON is the pTON zero address, which GeckoTerminal does
not index, so its decimals come from the local table below.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import ClassVar

from ezchain_core import Quote, QuoteKind, SourceRef

from .errors import NotFound, UpstreamError
from .quotes import (
    QUOTE_CACHE_TTL_SECONDS,
    HttpQuoteProvider,
    QuoteRequest,
    from_raw,
    parse_decimal,
    quote_price,
    stable_asset,
    to_raw,
)

BASE = "https://api.ston.fi"
NATIVE_TON = "EQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAM9c"
NATIVE_TON_DECIMALS = 9
SLIPPAGE_TOLERANCE = "0.01"


class StonfiClient(HttpQuoteProvider):
    label = "STON.fi"
    kind: ClassVar[QuoteKind] = "stonfi"
    base_url = BASE
    cache_ttl_seconds = QUOTE_CACHE_TTL_SECONDS
    rate_per_second = 1.0
    burst = 2
    # A 400/404 from STON.fi is "no pool for this jetton", not a broken provider.
    not_found_statuses = frozenset({400, 404})
    CHAINS = frozenset({"ton"})

    async def _quote(self, request: QuoteRequest) -> Quote:
        asset = stable_asset(request)
        if request.side == "buy":
            if request.amount_usd <= 0:
                raise NotFound("quote amount is zero")
            offer, ask = asset.address, request.token_address
            in_decimals = asset.decimals
            out_decimals = await self._decimals_for(request, request.token_address)
            amount_raw = to_raw(request.amount_usd, in_decimals)
        else:
            quantity = request.quantity
            if quantity is None or quantity <= 0:
                raise NotFound("sell requires a positive token quantity")
            offer, ask = request.token_address, asset.address
            in_decimals = await self._decimals_for(request, request.token_address)
            out_decimals = asset.decimals
            amount_raw = to_raw(quantity, in_decimals)
        if amount_raw <= 0:
            raise NotFound("quote amount is zero")

        payload = await self._post(
            "/v1/swap/simulate",
            {
                "offer_address": offer,
                "ask_address": ask,
                "units": str(amount_raw),
                "slippage_tolerance": SLIPPAGE_TOLERANCE,
                "dex_v2": "true",
            },
        )
        if not isinstance(payload, dict):
            raise UpstreamError("STON.fi returned an unexpected quote payload")
        if "ask_units" not in payload:
            raise NotFound(f"STON.fi has no route ({payload.get('error')})")
        out_raw = parse_decimal(payload.get("ask_units"))
        if out_raw is None or out_raw <= 0:
            raise NotFound("no route")

        amount_in = from_raw(amount_raw, in_decimals)
        amount_out = from_raw(int(out_raw), out_decimals)
        return Quote(
            chain=request.chain.id,
            token_in=offer,
            token_out=ask,
            side=request.side,
            amount_in=amount_in,
            amount_out=amount_out,
            price=quote_price(side=request.side, amount_in=amount_in, amount_out=amount_out),
            price_impact_pct=parse_decimal(payload.get("price_impact")),
            fee_usd=Decimal(0),
            kind=self.kind,
            route="STON.fi v2",
            source=SourceRef("stonfi", "https://ston.fi", datetime.now(UTC)),
            raw=payload,
        )

    async def _decimals_for(self, request: QuoteRequest, address: str) -> int:
        if address == NATIVE_TON:
            return NATIVE_TON_DECIMALS
        return await self._decimals.decimals(request.chain, address)
