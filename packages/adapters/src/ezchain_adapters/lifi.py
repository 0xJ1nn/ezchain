"""LI.FI ``/v1/quote`` adapter — the chain-coverage safety net.

Verified live 2026-10-22. LI.FI is the only keyless quote source for Mantle, Scroll,
zkSync, Cronos, Celo and Metis, and the last rung of the ladder on the majors.

It is never a primary where something cheaper exists, for two measured reasons:

* The free tier allows **75 requests per 2 hours** (one every ~96 s). The bucket is set to
  that rate with a burst of 3 and ``max_wait_seconds`` so an over-budget call fails fast
  instead of parking a request for minutes; the 60 s response cache absorbs repeats.
* It bakes a **0.25% fixed fee** into ``toAmount`` (``feeCosts[].percentage = "0.0025"``).
  For 1 ETH on Base, KyberSwap returned 2,394,131,326 raw USDC against LI.FI's
  2,386,192,236 — 0.33% worse. The fee is already inside the out amount; it is *declared*
  in ``fee_usd`` (together with gas) so the fill cost is visible, never double-charged.

``fromAddress`` must be a non-zero address even for a pure quote (a zero address is
rejected with ``code 1011``); a burn address is used because nothing is ever signed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, ClassVar

from ezchain_core import Quote, QuoteKind, SourceRef

from .errors import NotFound, UpstreamError
from .quotes import (
    HttpQuoteProvider,
    QuoteRequest,
    from_raw,
    parse_decimal,
    quote_price,
    stable_asset,
    to_raw,
    value_impact,
)

BASE = "https://li.quest/v1"
QUOTE_ADDRESS = "0x000000000000000000000000000000000000dEaD"
CACHE_TTL_SECONDS = 60.0
# 75 requests per 2 hours == 0.0104 rps.
RATE_PER_SECOND = 75 / 7200
BURST = 3
MAX_WAIT_SECONDS = 5.0


class LifiClient(HttpQuoteProvider):
    label = "LI.FI"
    kind: ClassVar[QuoteKind] = "lifi"
    base_url = BASE
    cache_ttl_seconds = CACHE_TTL_SECONDS
    rate_per_second = RATE_PER_SECOND
    burst = BURST
    limiter_max_wait_seconds = MAX_WAIT_SECONDS
    not_found_statuses = frozenset({400, 404})
    CHAINS = frozenset(
        {
            "ethereum",
            "optimism",
            "bsc",
            "polygon",
            "base",
            "arbitrum",
            "avalanche",
            "linea",
            "mantle",
            "scroll",
            "zksync",
            "cronos",
            "celo",
            "metis",
        }
    )

    async def _quote(self, request: QuoteRequest) -> Quote:
        asset = stable_asset(request)
        if request.side == "buy":
            if request.amount_usd <= 0:
                raise NotFound("quote amount is zero")
            from_token, to_token = asset.address, request.token_address
            from_decimals = asset.decimals
            to_decimals = await self._decimals.decimals(request.chain, request.token_address)
            amount_raw = to_raw(request.amount_usd, from_decimals)
        else:
            quantity = request.quantity
            if quantity is None or quantity <= 0:
                raise NotFound("sell requires a positive token quantity")
            from_token, to_token = request.token_address, asset.address
            from_decimals = await self._decimals.decimals(request.chain, request.token_address)
            to_decimals = asset.decimals
            amount_raw = to_raw(quantity, from_decimals)
        if amount_raw <= 0:
            raise NotFound("quote amount is zero")
        chain_id = request.chain.evm_chain_id
        if chain_id is None:  # pragma: no cover - supports() already rejects these chains
            raise NotFound(f"{request.chain.id} has no EVM chain id")

        payload = await self._get(
            "/quote",
            {
                "fromChain": str(chain_id),
                "toChain": str(chain_id),
                "fromToken": from_token,
                "toToken": to_token,
                "fromAmount": str(amount_raw),
                "fromAddress": QUOTE_ADDRESS,
                "integrator": "ezchain",
                "slippage": "0.005",
            },
        )
        estimate = _estimate(payload)
        out_raw = parse_decimal(estimate.get("toAmount"))
        if out_raw is None or out_raw <= 0:
            raise NotFound("no route")

        # LI.FI reports both tokens' decimals; trust them over a metadata lookup.
        to_decimals = _decimals_from(payload, "toToken", to_decimals)
        from_decimals = _decimals_from(payload, "fromToken", from_decimals)
        amount_in = from_raw(amount_raw, from_decimals)
        amount_out = from_raw(int(out_raw), to_decimals)
        from_usd = parse_decimal(estimate.get("fromAmountUSD"))
        to_usd = parse_decimal(estimate.get("toAmountUSD"))
        return Quote(
            chain=request.chain.id,
            token_in=from_token,
            token_out=to_token,
            side=request.side,
            amount_in=amount_in,
            amount_out=amount_out,
            price=quote_price(
                side=request.side,
                amount_in=amount_in,
                amount_out=amount_out,
                amount_in_usd=from_usd,
                amount_out_usd=to_usd,
            ),
            price_impact_pct=value_impact(amount_in_usd=from_usd, amount_out_usd=to_usd),
            fee_usd=_fee_usd(estimate),
            kind=self.kind,
            route=_route(payload),
            source=SourceRef("lifi", "https://li.fi", datetime.now(UTC)),
            raw=payload if isinstance(payload, dict) else {},
        )


def _estimate(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise UpstreamError("LI.FI returned an unexpected quote payload")
    if "message" in payload and "estimate" not in payload:
        # LI.FI answers 200 with {"message": ..., "code": 1003} when no route exists.
        raise NotFound(f"LI.FI has no route ({payload.get('message')})")
    estimate = payload.get("estimate")
    if not isinstance(estimate, dict):
        raise UpstreamError("LI.FI returned an unexpected quote payload")
    return estimate


def _decimals_from(payload: dict[str, Any], key: str, fallback: int) -> int:
    action = payload.get("action")
    token = action.get(key) if isinstance(action, dict) else None
    raw = token.get("decimals") if isinstance(token, dict) else None
    if isinstance(raw, bool) or raw is None:
        return fallback
    try:
        decimals = int(str(raw))
    except (TypeError, ValueError):
        return fallback
    return decimals if 0 <= decimals <= 36 else fallback


def _fee_usd(estimate: dict[str, Any]) -> Decimal:
    """Provider fee plus gas, both already priced in USD by LI.FI."""
    total = Decimal(0)
    for key in ("feeCosts", "gasCosts"):
        costs = estimate.get(key)
        if not isinstance(costs, list):
            continue
        for cost in costs:
            if not isinstance(cost, dict):
                continue
            amount = parse_decimal(cost.get("amountUSD"))
            if amount is not None and amount > 0:
                total += amount
    return total


def _route(payload: dict[str, Any]) -> str | None:
    tool = payload.get("tool")
    details = payload.get("toolDetails")
    name = details.get("name") if isinstance(details, dict) else None
    if isinstance(name, str) and name:
        return name
    return tool if isinstance(tool, str) and tool else None
