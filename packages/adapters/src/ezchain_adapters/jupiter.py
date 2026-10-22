"""Jupiter Lite quote adapter (Solana). No key, no signing, no swap transaction."""

from __future__ import annotations

import time
from collections import OrderedDict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from ezchain_core import Quote, SourceRef

from .cache import BoundedTTLCache
from .errors import NotFound, UpstreamError
from .http import DEFAULT_TIMEOUT_SECONDS, Clock, JsonApiClient, Sleeper
from .ratelimit import TokenBucket, default_sleep

LITE_BASE = "https://lite-api.jup.ag"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOL_MINT = "So11111111111111111111111111111111111111112"
CACHE_TTL_SECONDS = 5.0
DECIMALS_CACHE_MAX_ENTRIES = 512
MAX_TOKEN_DECIMALS = 18

USDC_DECIMALS = 6
SOL_DECIMALS = 9


def _dec(value: object) -> Decimal | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _int_field(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool):
        raise UpstreamError("Jupiter returned a malformed quote payload")
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        raise UpstreamError("Jupiter returned a malformed quote payload") from None
    if parsed < 0:
        raise UpstreamError("Jupiter returned a malformed quote payload")
    return parsed


def _route_labels(payload: dict[str, Any]) -> list[str]:
    plan = payload.get("routePlan")
    if not isinstance(plan, list):
        return []
    labels: list[str] = []
    for step in plan:
        if not isinstance(step, dict):
            continue
        info = step.get("swapInfo")
        if not isinstance(info, dict):
            continue
        label = info.get("label")
        if isinstance(label, str) and label and label not in labels:
            labels.append(label)
    return labels


class JupiterClient(JsonApiClient):
    label = "Jupiter"
    base_url = LITE_BASE
    cache_ttl_seconds = CACHE_TTL_SECONDS
    rate_per_second = 2.0
    burst = 4
    not_found_statuses = frozenset({400})

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        limiter: TokenBucket | None = None,
        cache: BoundedTTLCache | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleeper = default_sleep,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(
            client, limiter=limiter, cache=cache, clock=clock, sleep=sleep, timeout=timeout
        )
        self._decimals: OrderedDict[str, int] = OrderedDict(
            {USDC_MINT: USDC_DECIMALS, SOL_MINT: SOL_DECIMALS}
        )

    async def token_decimals(self, mint: str) -> int:
        cached = self._decimals.get(mint)
        if cached is not None:
            self._decimals.move_to_end(mint)
            return cached
        data = await self._get("/tokens/v2/search", {"query": mint})
        if not isinstance(data, list):
            raise UpstreamError("Jupiter returned an unexpected token search payload")
        for item in data:
            if not isinstance(item, dict) or item.get("id") != mint:
                continue
            raw = item.get("decimals")
            if isinstance(raw, bool):
                raise UpstreamError("Jupiter returned malformed token decimals")
            try:
                decimals = int(str(raw))
            except (TypeError, ValueError):
                raise UpstreamError("Jupiter returned malformed token decimals") from None
            if not 0 <= decimals <= MAX_TOKEN_DECIMALS:
                raise UpstreamError("Jupiter returned malformed token decimals")
            self._remember_decimals(mint, decimals)
            return decimals
        raise NotFound(f"unknown token decimals for {mint}")

    def _remember_decimals(self, mint: str, decimals: int) -> None:
        self._decimals[mint] = decimals
        self._decimals.move_to_end(mint)
        while len(self._decimals) > DECIMALS_CACHE_MAX_ENTRIES:
            self._decimals.popitem(last=False)

    async def quote_exact_in(
        self,
        *,
        input_mint: str,
        output_mint: str,
        amount_raw: int,
        slippage_bps: int = 50,
    ) -> dict[str, Any]:
        data = await self._get(
            "/swap/v1/quote",
            {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(amount_raw),
                "slippageBps": str(slippage_bps),
                "restrictIntermediateTokens": "true",
            },
        )
        if not isinstance(data, dict):
            raise UpstreamError("Jupiter returned an unexpected quote payload")
        return data

    async def quote_paper(
        self,
        *,
        token_address: str,
        side: str,
        amount_usd: Decimal,
        quantity: Decimal | None = None,
        slippage_bps: int = 50,
    ) -> tuple[Quote, int, int]:
        """Return (quote, input_decimals, output_decimals).

        Buy: USDC -> token, sized by amount_usd. Sell: token -> USDC, sized by quantity.

        ``price_impact_pct`` carries Jupiter's ``priceImpactPct`` unchanged: it is a
        ratio (``0.01`` means 1%), and formatting belongs to the UI layer.
        """
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        if side == "sell" and (quantity is None or quantity <= 0):
            raise NotFound("sell requires a positive token quantity")
        if side == "buy" and amount_usd <= 0:
            raise NotFound("quote amount is zero")

        token_decimals = await self.token_decimals(token_address)
        if side == "buy":
            input_mint, output_mint = USDC_MINT, token_address
            amount_raw = int(amount_usd * Decimal(10**USDC_DECIMALS))
            in_decimals, out_decimals = USDC_DECIMALS, token_decimals
        else:
            assert quantity is not None  # narrowed by the guard above
            input_mint, output_mint = token_address, USDC_MINT
            amount_raw = int(quantity * Decimal(10**token_decimals))
            in_decimals, out_decimals = token_decimals, USDC_DECIMALS
        if amount_raw <= 0:
            raise NotFound("quote amount is zero")

        raw = await self.quote_exact_in(
            input_mint=input_mint,
            output_mint=output_mint,
            amount_raw=amount_raw,
            slippage_bps=slippage_bps,
        )
        out_raw = _int_field(raw, "outAmount")
        if out_raw == 0:
            raise NotFound("no route")

        amount_in = Decimal(amount_raw) / Decimal(10**in_decimals)
        amount_out = Decimal(out_raw) / Decimal(10**out_decimals)
        price = (amount_in / amount_out) if side == "buy" else (amount_out / amount_in)
        labels = _route_labels(raw)
        return (
            Quote(
                chain="solana",
                token_in=input_mint,
                token_out=output_mint,
                side="buy" if side == "buy" else "sell",
                amount_in=amount_in,
                amount_out=amount_out,
                price=price,
                price_impact_pct=_dec(raw.get("priceImpactPct")),
                fee_usd=Decimal(0),
                kind="jupiter",
                route=" > ".join(labels) if labels else None,
                source=SourceRef("jupiter", "https://jup.ag", datetime.now(UTC)),
                raw=raw,
            ),
            in_decimals,
            out_decimals,
        )
