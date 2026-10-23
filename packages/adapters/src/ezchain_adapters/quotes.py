"""Quote routing: one verified ladder per chain, or an honest mark.

The registry owns *which* provider may quote a chain and in what order
(``ChainMeta.quote_providers``). This module owns the mechanics: ask each rung in turn,
reject a buy that has no full-size exit, and fail closed — never fall back to a synthetic
price — when every rung is down. No caller outside ``packages/adapters`` needs to know a
chain name to get a quote.

Conventions shared by every provider adapter:

* ``amount_in`` / ``amount_out`` are in **token units** (decimals applied), never raw.
* ``price`` is USD per token: the stable leg's USD value divided by the token amount.
* ``price_impact_pct`` is a ratio, exactly as Jupiter reports it (``0.01`` is 1%). A
  provider that does not publish one gets it derived from the USD legs it does publish;
  when neither exists it is ``None`` and risk treats it as unknown.
* ``fee_usd`` is what the quote itself costs (pool/aggregator fee plus gas when the
  provider prices gas in USD), not the platform's own paper fee.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar, Protocol, cast

import httpx

from ezchain_core import Quote, QuoteKind, Side, SourceRef, utcnow

from .breaker import CircuitBreaker
from .cache import BoundedTTLCache
from .decimals import DecimalsResolver
from .dexscreener import DexScreenerClient
from .errors import AdapterError, NoRoute, NotFound, UpstreamError
from .http import DEFAULT_TIMEOUT_SECONDS, Clock, JsonApiClient, Sleeper
from .ratelimit import TokenBucket, default_sleep
from .registry import BY_ID, PAPER_MARK, ChainMeta, QuoteAsset

QUOTE_CACHE_TTL_SECONDS = 10.0
# A trader should get a quote or a reason, not an open connection. The ladder can try
# several providers; the total budget is bounded so one dead rung cannot hold the request
# for the sum of every provider's HTTP timeout.
QUOTE_DEADLINE_SECONDS = 8.0
PROVIDER_ATTEMPT_SECONDS = 5.0
MAX_TOKEN_DECIMALS = 36


# ---------------------------------------------------------------------------
# Shared parsing helpers
# ---------------------------------------------------------------------------


def parse_decimal(value: object) -> Decimal | None:
    """Parse an optional decimal field; ``None``/blank/bool/non-finite become ``None``."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def parse_int(payload: dict[str, Any], key: str, *, label: str) -> int:
    """Read a non-negative integer field, or raise ``UpstreamError`` for a broken payload."""
    value = payload.get(key)
    if isinstance(value, bool) or value is None:
        raise UpstreamError(f"{label} returned a malformed quote payload")
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        raise UpstreamError(f"{label} returned a malformed quote payload") from None
    if parsed < 0:
        raise UpstreamError(f"{label} returned a malformed quote payload")
    return parsed


def to_raw(amount: Decimal, decimals: int) -> int:
    """Scale a token amount to raw integer units, truncating toward zero."""
    if not 0 <= decimals <= MAX_TOKEN_DECIMALS:
        raise NotFound("unknown token decimals")
    return int(amount * (Decimal(10) ** decimals))


def from_raw(raw: int, decimals: int) -> Decimal:
    if not 0 <= decimals <= MAX_TOKEN_DECIMALS:
        raise NotFound("unknown token decimals")
    return Decimal(raw) / (Decimal(10) ** decimals)


def quote_price(
    *,
    side: Side,
    amount_in: Decimal,
    amount_out: Decimal,
    amount_in_usd: Decimal | None = None,
    amount_out_usd: Decimal | None = None,
) -> Decimal:
    """USD per token, using the provider's USD legs when it publishes them."""
    if side == "buy":
        usd = amount_in_usd if amount_in_usd is not None else amount_in
        return usd / amount_out
    usd = amount_out_usd if amount_out_usd is not None else amount_out
    return usd / amount_in


def value_impact(
    *, amount_in_usd: Decimal | None, amount_out_usd: Decimal | None
) -> Decimal | None:
    """Derive a price-impact ratio from a provider's two USD legs.

    This is the value lost to pool fee and depth at the quoted size (a favourable fill can
    come out slightly negative). It is what KyberSwap, Velora and LI.FI leave to the
    integrator: none of them returns an impact field on the quote path.
    """
    if amount_in_usd is None or amount_out_usd is None or amount_in_usd <= 0:
        return None
    return (amount_out_usd / amount_in_usd) - 1


# ---------------------------------------------------------------------------
# Provider contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QuoteRequest:
    chain: ChainMeta
    token_address: str
    side: Side
    amount_usd: Decimal
    quantity: Decimal | None = None
    slippage_bps: int = 50


class QuoteProvider(Protocol):
    """One keyless quote source. ``kind`` is the value stored as ``Quote.kind``."""

    kind: ClassVar[QuoteKind]

    def supports(self, chain: ChainMeta) -> bool: ...

    async def quote(self, request: QuoteRequest) -> Quote: ...


class HttpQuoteProvider(JsonApiClient):
    """JSON quote provider with the shared cache, bucket and a 60 s circuit breaker."""

    kind: ClassVar[QuoteKind]
    breaker_cooldown_seconds: ClassVar[float] = 60.0
    # Registry chain ids this provider can quote. A ladder may list a provider that does
    # not cover a chain; ``supports`` is what keeps the router honest about that.
    CHAINS: ClassVar[frozenset[str]] = frozenset()

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        decimals: DecimalsResolver | None = None,
        limiter: TokenBucket | None = None,
        cache: BoundedTTLCache | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleeper = default_sleep,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        super().__init__(
            client, limiter=limiter, cache=cache, clock=clock, sleep=sleep, timeout=timeout
        )
        # A provider that cannot resolve decimals fails closed on that token; Jupiter
        # resolves its own mints and never asks.
        self._decimals = decimals or DecimalsResolver()
        self._breaker = breaker or CircuitBreaker(
            cooldown_seconds=self.breaker_cooldown_seconds, clock=clock
        )

    def supports(self, chain: ChainMeta) -> bool:
        return chain.id in self.CHAINS

    async def quote(self, request: QuoteRequest) -> Quote:
        if self._breaker.is_open():
            raise UpstreamError(f"{self.label} circuit open", detail="breaker")
        try:
            quote = await self._quote(request)
        except UpstreamError:
            # 5xx, 530, HTML/WAF and transport failures: the provider is unhealthy, not the
            # token. Open the circuit so the next order does not pay the same timeout.
            self._breaker.record_failure()
            raise
        self._breaker.record_success()
        return quote

    async def _quote(self, request: QuoteRequest) -> Quote:
        raise NotImplementedError


def stable_asset(request: QuoteRequest) -> QuoteAsset:
    """The chain's pricing stable, or a hard failure when the ladder has none."""
    asset = request.chain.quote_asset
    if asset is None:  # pragma: no cover - registry rejects an executable ladder without one
        raise NotFound(f"{request.chain.id} has no quote asset")
    return asset


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class QuoteRouter:
    """Walk one chain's ladder and return the first quote that can also be exited."""

    def __init__(
        self, *, providers: Iterable[QuoteProvider], dexscreener: DexScreenerClient
    ) -> None:
        self._providers: dict[str, QuoteProvider] = {
            provider.kind: provider for provider in providers
        }
        self._dexscreener = dexscreener

    async def quote(
        self,
        *,
        chain: str,
        token_address: str,
        side: str,
        amount_usd: Decimal,
        quantity: Decimal | None = None,
    ) -> Quote:
        meta = BY_ID.get(chain)
        if meta is None:
            raise NotFound(f"unknown chain {chain}")
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        typed_side = cast(Side, side)
        if typed_side == "buy" and amount_usd <= 0:
            raise NotFound("quote amount is zero")
        if typed_side == "sell" and (quantity is None or quantity <= 0):
            quantity = await self._quantity_from_usd(meta, token_address, amount_usd)

        request = QuoteRequest(
            chain=meta,
            token_address=token_address,
            side=typed_side,
            amount_usd=amount_usd,
            quantity=quantity,
        )
        failures: list[str] = []
        deadline = time.monotonic() + QUOTE_DEADLINE_SECONDS
        for kind in meta.quote_providers:
            if kind == PAPER_MARK:
                break
            provider = self._providers.get(kind)
            if provider is None or not provider.supports(meta):
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failures.append("deadline")
                break
            try:
                quote = await asyncio.wait_for(
                    provider.quote(request), timeout=min(remaining, PROVIDER_ATTEMPT_SECONDS)
                )
            except TimeoutError:
                failures.append(f"{kind}:TIMEOUT")
                continue
            except AdapterError as exc:
                failures.append(f"{kind}:{exc.code}")
                continue
            if quote.amount_out <= 0:
                failures.append(f"{kind}:NO_ROUTE")
                continue
            if typed_side == "buy":
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failures.append("deadline")
                    break
                try:
                    exit_ok = await asyncio.wait_for(
                        self._has_exit(provider, request, quote),
                        timeout=min(remaining, PROVIDER_ATTEMPT_SECONDS),
                    )
                except TimeoutError:
                    failures.append(f"{kind}:EXIT_TIMEOUT")
                    continue
                if not exit_ok:
                    # An entry with no executable exit is not an entry (ADR-0015), so try
                    # the next rung rather than book a position that cannot be closed.
                    failures.append(f"{kind}:NO_SELL_ROUTE")
                    continue
            return quote

        if meta.quote_providers[0] == PAPER_MARK:
            remaining = max(0.1, deadline - time.monotonic())
            try:
                return await asyncio.wait_for(self._paper_mark(request), timeout=remaining)
            except TimeoutError as exc:
                raise NoRoute(
                    "paper mark timed out",
                    detail=f"deadline={QUOTE_DEADLINE_SECONDS:g}s",
                    code="NO_ROUTE",
                ) from exc
        # Fail closed. A chain with an executable ladder never degrades into a mark: a
        # silent downgrade would book a fill the venue could not have produced.
        # A buy that only failed the full-size-exit check is a NO_SELL_ROUTE; a ladder
        # that timed out or had every rung unavailable is a NO_ROUTE. The trader sees the
        # difference between "cannot exit this token" and "no venue answered".
        code = (
            "NO_SELL_ROUTE"
            if failures and all(item.endswith(":NO_SELL_ROUTE") for item in failures)
            else "NO_ROUTE"
        )
        raise NoRoute("no executable quote", detail=", ".join(failures) or None, code=code)

    async def _has_exit(self, provider: QuoteProvider, request: QuoteRequest, quote: Quote) -> bool:
        """The received size must have a full-size sell quote from the same venue."""
        exit_request = replace(
            request,
            side="sell",
            quantity=quote.amount_out,
            amount_usd=quote.amount_out * quote.price,
        )
        try:
            exit_quote = await provider.quote(exit_request)
        except AdapterError:
            return False
        return exit_quote.amount_out > 0

    async def _quantity_from_usd(
        self, meta: ChainMeta, token_address: str, amount_usd: Decimal
    ) -> Decimal:
        snapshot = await self._dexscreener.get_token(meta.id, token_address)
        price = snapshot.price_usd
        if price is None or price <= 0:
            raise NoRoute("sell requires a positive price")
        return amount_usd / price

    async def _paper_mark(self, request: QuoteRequest) -> Quote:
        """The capped DexScreener mark for chains with no keyless executable source."""
        snapshot = await self._dexscreener.get_token(request.chain.id, request.token_address)
        price = snapshot.price_usd
        if price is None or price <= 0:
            raise NoRoute("no executable paper mark")
        if request.side == "buy":
            amount_in = request.amount_usd
            amount_out = request.amount_usd / price
            token_in, token_out = (
                _stable_symbol(request.chain),
                request.token_address,
            )
        else:
            quantity = request.quantity
            if quantity is None or quantity <= 0:
                quantity = request.amount_usd / price
            amount_in, amount_out = quantity, quantity * price
            token_in, token_out = (
                request.token_address,
                _stable_symbol(request.chain),
            )
        source = snapshot.source or SourceRef("dexscreener", "https://dexscreener.com", utcnow())
        return Quote(
            chain=request.chain.id,
            token_in=token_in,
            token_out=token_out,
            side=request.side,
            amount_in=amount_in,
            amount_out=amount_out,
            price=price,
            price_impact_pct=None,
            fee_usd=Decimal(0),
            kind=PAPER_MARK,
            route=None,
            source=source,
        )


def _stable_symbol(chain: ChainMeta) -> str:
    """The stable leg's label for a mark; chains without one quote against USDC."""
    asset = chain.quote_asset
    return asset.symbol if asset is not None else "USDC"
