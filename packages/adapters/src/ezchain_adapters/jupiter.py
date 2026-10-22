"""Jupiter Lite quote adapter (Solana). No key, no signing, no swap transaction."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from ezchain_core import Quote, SourceRef

from .errors import NotFound, RateLimited, UpstreamError

LITE_BASE = "https://lite-api.jup.ag"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOL_MINT = "So11111111111111111111111111111111111111112"
CACHE_TTL_SECONDS = 5.0


class _RateLimiter:
    def __init__(self, rate_per_second: float = 2.0, burst: int = 4) -> None:
        self.rate = rate_per_second
        self.burst = burst
        self._times: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self._times = [t for t in self._times if now - t <= 1.0]
            if len(self._times) >= self.burst:
                wait = 1.0 - (now - self._times[0])
                if wait > 0:
                    await asyncio.sleep(wait)
            self._times.append(time.monotonic())


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (ValueError, ArithmeticError):
        return None


class JupiterClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None
        self._limiter = _RateLimiter()
        self._cache: dict[str, tuple[float, Any]] = {}
        self._decimals: dict[str, int] = {USDC_MINT: 6, SOL_MINT: 9}

    async def __aenter__(self) -> JupiterClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=LITE_BASE,
                timeout=httpx.Timeout(15.0),
                headers={"User-Agent": "ezCHAIN/0.1 (+https://github.com/0xJ1nn/ezchain)"},
            )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        key = f"{path}?{sorted(params.items())}"
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
        await self._limiter.acquire()
        assert self._client is not None
        try:
            response = await self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise UpstreamError(f"Jupiter request failed: {exc}") from exc
        if response.status_code == 429:
            raise RateLimited("Jupiter rate limit")
        if response.status_code == 400:
            raise NotFound("Jupiter has no route")
        if response.status_code >= 400:
            raise UpstreamError(f"Jupiter returned {response.status_code}")
        data = response.json()
        self._cache[key] = (time.monotonic() + CACHE_TTL_SECONDS, data)
        return data

    async def token_decimals(self, mint: str) -> int:
        if mint in self._decimals:
            return self._decimals[mint]
        data = await self._get("/tokens/v2/search", {"query": mint})
        if isinstance(data, list):
            for item in data:
                if item.get("id") == mint and item.get("decimals") is not None:
                    decimals = int(item["decimals"])
                    self._decimals[mint] = decimals
                    return decimals
        raise NotFound(f"unknown token decimals for {mint}")

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
        """
        token_decimals = await self.token_decimals(token_address)
        if side == "buy":
            input_mint, output_mint = USDC_MINT, token_address
            amount_raw = int(amount_usd * Decimal(10**6))
            in_decimals, out_decimals = 6, token_decimals
        else:
            if quantity is None or quantity <= 0:
                raise NotFound("sell requires a positive token quantity")
            input_mint, output_mint = token_address, USDC_MINT
            amount_raw = int(quantity * Decimal(10**token_decimals))
            in_decimals, out_decimals = token_decimals, 6
        if amount_raw <= 0:
            raise NotFound("quote amount is zero")
        raw = await self.quote_exact_in(
            input_mint=input_mint,
            output_mint=output_mint,
            amount_raw=amount_raw,
            slippage_bps=slippage_bps,
        )
        out_raw = int(raw.get("outAmount", "0"))
        if out_raw <= 0:
            raise NotFound("no route")
        amount_in = Decimal(amount_raw) / Decimal(10**in_decimals)
        amount_out = Decimal(out_raw) / Decimal(10**out_decimals)
        price = (amount_in / amount_out) if side == "buy" else (amount_out / amount_in)
        labels = []
        for step in raw.get("routePlan") or []:
            info = step.get("swapInfo") or {}
            label = info.get("label")
            if label and label not in labels:
                labels.append(str(label))
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
