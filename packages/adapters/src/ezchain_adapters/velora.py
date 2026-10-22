"""Velora (formerly ParaSwap) ``/prices`` quote adapter — the EVM fallback.

Verified live 2026-10-22. ``GET /networks?type=evm`` lists exactly 11 chains
(1, 10, 56, 100, 130, 137, 146, 4663, 8453, 42161, 43114); Linea, Scroll, zkSync, Mantle,
Celo, Cronos, Metis and Blast are gone from the API even though older tutorials still list
them. Only registered chains appear in ``CHAINS``.

Two details the quote path must not lose:

* Velora applies a default 1 bp ``partnerFee`` (``partner: "anon"``) that makes
  ``destAmount`` differ from ``destAmountAfterFee``. This adapter asks for a zero partner
  fee (``partner=ezchain&partnerFeeBps=0``) and still uses ``destAmountAfterFee`` when the
  API returns one, then declares any residual fee in ``fee_usd``.
* Velora publishes no numeric price impact: it *enforces* a 15% budget and reports
  ``maxImpactReached``. A reached budget is treated as no route; otherwise the impact is
  derived from ``destUSD / srcUSD - 1``.
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
    quote_price,
    stable_asset,
    to_raw,
    value_impact,
)

BASE = "https://api.paraswap.io"
PARTNER = "ezchain"


class VeloraClient(HttpQuoteProvider):
    label = "Velora"
    kind: ClassVar[QuoteKind] = "velora"
    base_url = BASE
    cache_ttl_seconds = QUOTE_CACHE_TTL_SECONDS
    rate_per_second = 1.0  # no published figure; plan for ~1-2 rps and back off on 429
    burst = 1
    not_found_statuses = frozenset({400, 404})
    CHAINS = frozenset({"ethereum", "optimism", "bsc", "polygon", "base", "arbitrum", "avalanche"})

    async def _quote(self, request: QuoteRequest) -> Quote:
        asset = stable_asset(request)
        if request.side == "buy":
            if request.amount_usd <= 0:
                raise NotFound("quote amount is zero")
            src_token, dest_token = asset.address, request.token_address
            src_decimals = asset.decimals
            dest_decimals = await self._decimals.decimals(request.chain, request.token_address)
            amount_raw = to_raw(request.amount_usd, src_decimals)
        else:
            quantity = request.quantity
            if quantity is None or quantity <= 0:
                raise NotFound("sell requires a positive token quantity")
            src_token, dest_token = request.token_address, asset.address
            src_decimals = await self._decimals.decimals(request.chain, request.token_address)
            dest_decimals = asset.decimals
            amount_raw = to_raw(quantity, src_decimals)
        if amount_raw <= 0:
            raise NotFound("quote amount is zero")
        network = request.chain.evm_chain_id
        if network is None:  # pragma: no cover - supports() already rejects these chains
            raise NotFound(f"{request.chain.id} has no EVM chain id")

        payload = await self._get(
            "/prices",
            {
                "srcToken": src_token,
                "destToken": dest_token,
                "amount": str(amount_raw),
                "srcDecimals": str(src_decimals),
                "destDecimals": str(dest_decimals),
                "network": str(network),
                # Exact-in in both directions: a sell is the same call with the tokens
                # swapped, never an inverted buy quote.
                "side": "SELL",
                "partner": PARTNER,
                "partnerFeeBps": "0",
                "version": "5",
            },
        )
        price_route = _price_route(payload)
        out_raw = parse_decimal(price_route.get("destAmountAfterFee")) or parse_decimal(
            price_route.get("destAmount")
        )
        if out_raw is None or out_raw <= 0:
            raise NotFound("no route")

        amount_in = from_raw(amount_raw, src_decimals)
        amount_out = from_raw(int(out_raw), dest_decimals)
        src_usd = parse_decimal(price_route.get("srcUSD"))
        dest_usd = parse_decimal(price_route.get("destUSD"))
        return Quote(
            chain=request.chain.id,
            token_in=src_token,
            token_out=dest_token,
            side=request.side,
            amount_in=amount_in,
            amount_out=amount_out,
            price=quote_price(
                side=request.side,
                amount_in=amount_in,
                amount_out=amount_out,
                amount_in_usd=src_usd,
                amount_out_usd=dest_usd,
            ),
            price_impact_pct=value_impact(amount_in_usd=src_usd, amount_out_usd=dest_usd),
            fee_usd=_fee_usd(price_route, dest_usd=dest_usd),
            kind=self.kind,
            route=_route(price_route),
            source=SourceRef("velora", "https://velora.xyz", datetime.now(UTC)),
            raw=payload if isinstance(payload, dict) else {},
        )


def _price_route(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise UpstreamError("Velora returned an unexpected quote payload")
    route = payload.get("priceRoute")
    if not isinstance(route, dict):
        raise UpstreamError("Velora returned an unexpected quote payload")
    if route.get("maxImpactReached") is True:
        raise NotFound("Velora impact budget reached")
    return route


def _fee_usd(price_route: dict[str, Any], *, dest_usd: Decimal | None) -> Decimal:
    """Gas plus any residual partner fee the API still applied to the out amount."""
    gas = parse_decimal(price_route.get("gasCostUSD")) or Decimal(0)
    fee = gas if gas > 0 else Decimal(0)
    after_fee = parse_decimal(price_route.get("destAmountAfterFee"))
    gross = parse_decimal(price_route.get("destAmount"))
    if after_fee is not None and gross is not None and gross > after_fee > 0:
        share = (gross - after_fee) / gross
        if dest_usd is not None and dest_usd > 0:
            fee += dest_usd * share
    return fee


def _route(price_route: dict[str, Any]) -> str | None:
    best = price_route.get("bestRoute")
    if not isinstance(best, list):
        return None
    venues: list[str] = []
    for split in best:
        if not isinstance(split, dict):
            continue
        for swap in split.get("swaps") or []:
            if not isinstance(swap, dict):
                continue
            for exchange in swap.get("swapExchanges") or []:
                if not isinstance(exchange, dict):
                    continue
                name = exchange.get("exchange")
                if isinstance(name, str) and name and name not in venues:
                    venues.append(name)
    return " > ".join(venues) if venues else None
