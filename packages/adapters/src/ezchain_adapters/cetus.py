"""Cetus Aggregator v2 quote adapter (Sui, keyless).

Verified live 2026-10-22: ``GET /router_v2/find_routes`` with ``from``/``target`` coin types
and ``byAmountIn=true``. The response carries ``amount_out`` (raw) and
``deviation_ratio`` — a signed ratio that *is* the price impact (``-0.00099`` is −0.1%).

Cetus reports no decimals and no USD gas price (``gas`` is in MIST), so decimals come from
the shared resolver and ``fee_usd`` stays zero rather than inventing a SUI price. The raw
gas figure stays in ``raw`` for anyone who wants to price it.

No documented rate limit: paced at 1 rps to stay a polite client.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, ClassVar

from ezchain_core import Quote, QuoteKind, SourceRef

from .errors import NotFound, UpstreamError
from .quotes import (
    QUOTE_CACHE_TTL_SECONDS,
    HttpQuoteProvider,
    QuoteRequest,
    from_raw,
    parse_decimal,
    parse_int,
    quote_price,
    stable_asset,
    to_raw,
)

BASE = "https://api-sui.cetus.zone"
DEPTH = 3


class CetusClient(HttpQuoteProvider):
    label = "Cetus"
    kind: ClassVar[QuoteKind] = "cetus"
    base_url = BASE
    cache_ttl_seconds = QUOTE_CACHE_TTL_SECONDS
    rate_per_second = 1.0
    burst = 2
    CHAINS = frozenset({"sui"})

    async def _quote(self, request: QuoteRequest) -> Quote:
        asset = stable_asset(request)
        if request.side == "buy":
            if request.amount_usd <= 0:
                raise NotFound("quote amount is zero")
            coin_from, coin_to = asset.address, request.token_address
            in_decimals = asset.decimals
            out_decimals = await self._decimals.decimals(request.chain, request.token_address)
            amount_raw = to_raw(request.amount_usd, in_decimals)
        else:
            quantity = request.quantity
            if quantity is None or quantity <= 0:
                raise NotFound("sell requires a positive token quantity")
            coin_from, coin_to = request.token_address, asset.address
            in_decimals = await self._decimals.decimals(request.chain, request.token_address)
            out_decimals = asset.decimals
            amount_raw = to_raw(quantity, in_decimals)
        if amount_raw <= 0:
            raise NotFound("quote amount is zero")

        payload = await self._get(
            "/router_v2/find_routes",
            {
                "from": coin_from,
                "target": coin_to,
                "amount": str(amount_raw),
                "byAmountIn": "true",
                "depth": str(DEPTH),
            },
        )
        data = _data(payload)
        out_raw = parse_int(data, "amount_out", label=self.label)
        if out_raw <= 0:
            raise NotFound("no route")

        amount_in = from_raw(amount_raw, in_decimals)
        amount_out = from_raw(out_raw, out_decimals)
        return Quote(
            chain=request.chain.id,
            token_in=coin_from,
            token_out=coin_to,
            side=request.side,
            amount_in=amount_in,
            amount_out=amount_out,
            price=quote_price(side=request.side, amount_in=amount_in, amount_out=amount_out),
            price_impact_pct=parse_decimal(data.get("deviation_ratio")),
            fee_usd=Decimal(0),
            kind=self.kind,
            route=_route(data),
            source=SourceRef("cetus", "https://cetus.zone", datetime.now(UTC)),
            raw=payload if isinstance(payload, dict) else {},
        )


def _data(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise UpstreamError("Cetus returned an unexpected quote payload")
    code = payload.get("code")
    if code != 200:
        raise NotFound(f"Cetus has no route ({payload.get('msg')})")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise UpstreamError("Cetus returned an unexpected quote payload")
    return data


def _route(data: dict[str, Any]) -> str | None:
    routes = data.get("routes")
    if not isinstance(routes, list):
        return None
    venues: list[str] = []
    for route in routes:
        path = route.get("path") if isinstance(route, dict) else None
        if not isinstance(path, list):
            continue
        for hop in path:
            provider = hop.get("provider") if isinstance(hop, dict) else None
            if isinstance(provider, str) and provider and provider not in venues:
                venues.append(provider)
    return " > ".join(venues) if venues else None
