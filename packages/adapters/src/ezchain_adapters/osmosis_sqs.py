"""Osmosis SQS ``/router/quote`` adapter (Cosmos, keyless).

Verified live 2026-10-22. The ``tokenIn`` parameter is **amount and denom concatenated**
(``1000000uosmo``); a bare denom is rejected with ``tokenIn is invalid - must be in the
format amountDenom``. ``tokenOutDenom`` is the denom alone, and an IBC asset needs its full
``ibc/<HASH>`` form.

The response is unusually honest: ``price_impact`` is first-class, every pool in the split
route is named with its ``taker_fee``/``spread_factor``, and ``liquidity_cap_overflow``
says SQS refused to route past its safety cap. That overflow is treated as no route: a
quote beyond the cap is not a fill anyone could have received.

Decimals: SQS reports none and GeckoTerminal does not index the Osmosis network at all
(``/networks/osmosis`` is a 404), so the denoms this adapter can price come from the table
below. Anything else fails closed with ``NOT_FOUND`` rather than guessing a power of ten.
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
)

BASE = "https://sqs.osmosis.zone"

# Native and canonical stable denoms. Decimals are a chain fact, not a per-request one.
DENOM_DECIMALS: dict[str, int] = {
    "uosmo": 6,
    "uatom": 6,
    "uusdc": 6,
    # USDC (Noble) on Osmosis, the same denom the registry uses as this chain's stable.
    "ibc/27394FB092D2ECCD56123C74F36E4C1F926001CEADA9CA97EA622B25F41E5EB2": 6,
}


class OsmosisSqsClient(HttpQuoteProvider):
    label = "Osmosis SQS"
    kind: ClassVar[QuoteKind] = "osmosis_sqs"
    base_url = BASE
    cache_ttl_seconds = QUOTE_CACHE_TTL_SECONDS
    rate_per_second = 1.0
    burst = 2
    CHAINS = frozenset({"osmosis"})

    async def _quote(self, request: QuoteRequest) -> Quote:
        asset = stable_asset(request)
        if request.side == "buy":
            if request.amount_usd <= 0:
                raise NotFound("quote amount is zero")
            denom_in, denom_out = asset.address, request.token_address
            in_decimals = asset.decimals
            out_decimals = _decimals_for(request.token_address)
            amount_raw = to_raw(request.amount_usd, in_decimals)
        else:
            quantity = request.quantity
            if quantity is None or quantity <= 0:
                raise NotFound("sell requires a positive token quantity")
            denom_in, denom_out = request.token_address, asset.address
            in_decimals = _decimals_for(request.token_address)
            out_decimals = asset.decimals
            amount_raw = to_raw(quantity, in_decimals)
        if amount_raw <= 0:
            raise NotFound("quote amount is zero")

        payload = await self._get(
            "/router/quote",
            {"tokenIn": f"{amount_raw}{denom_in}", "tokenOutDenom": denom_out},
        )
        data = _payload(payload)
        out_raw = parse_decimal(data.get("amount_out"))
        if out_raw is None or out_raw <= 0:
            raise NotFound("no route")

        amount_in = from_raw(amount_raw, in_decimals)
        amount_out = from_raw(int(out_raw), out_decimals)
        return Quote(
            chain=request.chain.id,
            token_in=denom_in,
            token_out=denom_out,
            side=request.side,
            amount_in=amount_in,
            amount_out=amount_out,
            price=quote_price(side=request.side, amount_in=amount_in, amount_out=amount_out),
            price_impact_pct=parse_decimal(data.get("price_impact")),
            fee_usd=Decimal(0),
            kind=self.kind,
            route=_route(data),
            source=SourceRef("osmosis_sqs", "https://sqs.osmosis.zone", datetime.now(UTC)),
            raw=payload if isinstance(payload, dict) else {},
        )


def _decimals_for(denom: str) -> int:
    decimals = DENOM_DECIMALS.get(denom)
    if decimals is None:
        raise NotFound(f"unknown decimals for Osmosis denom {denom}")
    return decimals


def _payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise UpstreamError("Osmosis SQS returned an unexpected quote payload")
    if "amount_out" not in payload:
        message = payload.get("message") or payload.get("error")
        raise NotFound(f"Osmosis SQS has no route ({message})")
    if payload.get("liquidity_cap_overflow") is True:
        raise NotFound("Osmosis SQS refused to route past its liquidity cap")
    return payload


def _route(data: dict[str, Any]) -> str | None:
    route = data.get("route")
    if not isinstance(route, list):
        return None
    pools: list[str] = []
    for split in route:
        for pool in (split.get("pools") or []) if isinstance(split, dict) else []:
            if not isinstance(pool, dict):
                continue
            pool_id = pool.get("id")
            if pool_id is None:
                continue
            label = f"pool-{pool_id}"
            if label not in pools:
                pools.append(label)
    return " > ".join(pools) if pools else None
