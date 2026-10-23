"""KyberSwap Aggregator v1 quote adapter — the primary keyless EVM source.

Verified live 2026-10-22: ``GET /{chain}/api/v1/routes`` answers for Ethereum, Base,
Arbitrum, Polygon, BNB Chain, Optimism, Avalanche and Linea (Sonic too, but Sonic is not a
registered chain). Blast, Mantle, Scroll, zkSync, Cronos, Celo, Moonbeam and Metis return
404: those chains were retired from the aggregator, so they are not in ``CHAINS``.

Two properties of this API shape the adapter:

* Errors are HTTP 200 with ``code != 0`` (``4008`` route not found, ``4011`` token not
  found, ``4000`` invalid token). A non-zero code is a fact about the pair, not an outage,
  so it becomes ``NotFound`` and the ladder moves on without opening the breaker.
* There is **no price-impact field**. Impact is derived from ``amountOutUsd /
  amountInUsd - 1``, which is the value lost to pool fee and depth at the quoted size.

Public rate limit is 3 rps; the bucket runs at 2 rps with a burst of 2 to stay clear of it.
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
    value_impact,
)

BASE = "https://aggregator-api.kyberswap.com"


class KyberSwapClient(HttpQuoteProvider):
    label = "KyberSwap"
    kind: ClassVar[QuoteKind] = "kyberswap"
    base_url = BASE
    cache_ttl_seconds = QUOTE_CACHE_TTL_SECONDS
    rate_per_second = 2.0
    burst = 2
    # A 404 means "this chain slug is not on the aggregator"; it must not open the breaker.
    not_found_statuses = frozenset({400, 404})
    CHAINS = frozenset(
        {"ethereum", "base", "arbitrum", "polygon", "bsc", "optimism", "avalanche", "linea"}
    )

    async def _quote(self, request: QuoteRequest) -> Quote:
        asset = stable_asset(request)
        if request.side == "buy":
            if request.amount_usd <= 0:
                raise NotFound("quote amount is zero")
            token_in, token_out = asset.address, request.token_address
            in_decimals = asset.decimals
            out_decimals = await self._decimals.decimals(request.chain, request.token_address)
            amount_in_raw = to_raw(request.amount_usd, in_decimals)
        else:
            quantity = request.quantity
            if quantity is None or quantity <= 0:
                raise NotFound("sell requires a positive token quantity")
            token_in, token_out = request.token_address, asset.address
            in_decimals = await self._decimals.decimals(request.chain, request.token_address)
            out_decimals = asset.decimals
            amount_in_raw = to_raw(quantity, in_decimals)
        if amount_in_raw <= 0:
            raise NotFound("quote amount is zero")

        payload = await self._get(
            f"/{request.chain.id}/api/v1/routes",
            {
                "tokenIn": token_in,
                "tokenOut": token_out,
                "amountIn": str(amount_in_raw),
                "gasInclude": "true",
            },
        )
        summary = _route_summary(payload)
        out_raw = parse_int(summary, "amountOut", label=self.label)
        if out_raw <= 0:
            raise NotFound("no route")

        amount_in = from_raw(amount_in_raw, in_decimals)
        amount_out = from_raw(out_raw, out_decimals)
        amount_in_usd = parse_decimal(summary.get("amountInUsd"))
        amount_out_usd = parse_decimal(summary.get("amountOutUsd"))
        return Quote(
            chain=request.chain.id,
            token_in=token_in,
            token_out=token_out,
            side=request.side,
            amount_in=amount_in,
            amount_out=amount_out,
            price=quote_price(
                side=request.side,
                amount_in=amount_in,
                amount_out=amount_out,
                amount_in_usd=amount_in_usd,
                amount_out_usd=amount_out_usd,
            ),
            price_impact_pct=value_impact(
                amount_in_usd=amount_in_usd, amount_out_usd=amount_out_usd
            ),
            fee_usd=_gas_usd(summary),
            kind=self.kind,
            route=_route(summary),
            source=SourceRef("kyberswap", "https://kyberswap.com", datetime.now(UTC)),
            raw=payload if isinstance(payload, dict) else {},
        )


def _route_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise UpstreamError("KyberSwap returned an unexpected quote payload")
    code = payload.get("code")
    if code != 0:
        raise NotFound(f"KyberSwap has no route ({payload.get('message')})")
    data = payload.get("data")
    summary = data.get("routeSummary") if isinstance(data, dict) else None
    if not isinstance(summary, dict):
        raise UpstreamError("KyberSwap returned an unexpected quote payload")
    return summary


def _gas_usd(summary: dict[str, Any]) -> Decimal:
    """Gas plus the OP-stack L1 data fee; both are costs the fill really pays."""
    gas = parse_decimal(summary.get("gasUsd")) or Decimal(0)
    l1_fee = parse_decimal(summary.get("l1FeeUsd")) or Decimal(0)
    total = gas + l1_fee
    return total if total > 0 else Decimal(0)


def _route(summary: dict[str, Any]) -> str | None:
    hops = summary.get("route")
    if not isinstance(hops, list):
        return None
    venues: list[str] = []
    for leg in hops:
        if not isinstance(leg, list):
            continue
        for hop in leg:
            if not isinstance(hop, dict):
                continue
            exchange = hop.get("exchange")
            if isinstance(exchange, str) and exchange and exchange not in venues:
                venues.append(exchange)
    return " > ".join(venues) if venues else None
