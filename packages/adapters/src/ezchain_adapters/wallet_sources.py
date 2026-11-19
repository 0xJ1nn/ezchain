"""Read-only sources of observed wallet activity.

A source answers one question: what did this address do recently? It never signs,
never trades and never decides. The identity of an observation is its source plus
the source's own transaction id, which is what lets the API dedupe a refresh
without guessing. A missing USD value stays ``None`` — unknown is not zero, and a
wallet with an unpriced swap reports unknown performance rather than a fabricated
one (CONTRACT I3).

Public endpoints come first. Each source is paced, cached and circuit-broken like
the market providers; a paid source accepts only the person's own key, which the
API resolves from the encrypted provider-key store. No test reaches the network.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import httpx

from .breaker import CircuitBreaker
from .cache import MISSING, BoundedTTLCache
from .errors import UpstreamError
from .ratelimit import TokenBucket, default_sleep

RpcCall = Callable[[str, list[Any]], Awaitable[dict[str, Any]]]
GetCall = Callable[[str, dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class ObservedSwap:
    """One swap an external source says an address made."""

    source: str
    source_tx: str
    chain: str
    side: str
    token_address: str
    token_symbol: str | None
    amount_tokens: Decimal | None
    amount_usd: Decimal | None
    price_usd: Decimal | None
    observed_at: datetime


class WalletActivitySource(Protocol):
    """A pluggable source of observed swaps."""

    name: str
    cost: str

    async def fetch_swaps(
        self, *, chain: str, address: str, since: datetime, limit: int
    ) -> list[ObservedSwap]: ...


def _get(raw: object, key: str, default: Any = None) -> Any:
    if isinstance(raw, Mapping):
        return raw.get(key, default)
    return getattr(raw, key, default)


def _dec(value: object) -> Decimal | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _stamp(value: object) -> datetime | None:
    # Explorers send unix time as a decimal string; Solana sends a JSON integer.
    if isinstance(value, str):
        try:
            value = int(value)
        except ValueError:
            return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


async def _http_rpc(method: str, params: list[Any]) -> dict[str, Any]:
    """The default public Solana JSON-RPC call. Tests inject a fake instead."""
    url = "https://api.mainnet-beta.solana.com"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            headers={"User-Agent": "ezCHAIN/0.1"},
        )
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise UpstreamError("Solana RPC returned a non-object response")
    if payload.get("error") is not None:
        raise UpstreamError("Solana RPC refused a request")
    return payload


class SolanaPublicRpcSource:
    """Signatures and parsed transactions from a public Solana RPC endpoint.

    Signatures are cached and paced; a failed signature, one outside the window
    and a transaction whose token transfer cannot be parsed are skipped, never
    filled in. The source is free and read-only.
    """

    name = "solana-rpc"
    cost = "free"

    def __init__(
        self,
        rpc: RpcCall | None = None,
        *,
        limiter: TokenBucket | None = None,
        cache: BoundedTTLCache | None = None,
        breaker: CircuitBreaker | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = default_sleep,
    ) -> None:
        self._rpc = rpc or _http_rpc
        self._limiter = limiter or TokenBucket(
            rate_per_second=5, burst=5, clock=clock, sleep=sleep, max_wait_seconds=5
        )
        self._cache = cache or BoundedTTLCache(max_entries=256, ttl_seconds=15, clock=clock)
        self._breaker = breaker or CircuitBreaker(cooldown_seconds=60, failure_threshold=3)

    async def _call(self, method: str, params: list[Any]) -> dict[str, Any]:
        if self._breaker.is_open():
            raise UpstreamError("Solana RPC source is unavailable")
        try:
            await self._limiter.acquire()
            payload = await self._rpc(method, params)
        except Exception:
            self._breaker.record_failure()
            raise
        self._breaker.record_success()
        return payload

    async def fetch_swaps(
        self, *, chain: str, address: str, since: datetime, limit: int
    ) -> list[ObservedSwap]:
        key = f"{chain}:{address}:{since.isoformat()}:{limit}"
        cached = self._cache.get(key)
        if cached is not MISSING:
            return list(cached)
        signatures = await self._call(
            "getSignaturesForAddress", [address, {"limit": limit, "until": since.isoformat()}]
        )
        rows = signatures.get("result")
        if not isinstance(rows, list):
            return []
        swaps: list[ObservedSwap] = []
        for item in rows:
            signature = _text(_get(item, "signature"))
            observed = _stamp(_get(item, "blockTime"))
            if signature is None or observed is None or observed < since:
                continue
            if _get(item, "err") is not None:
                continue
            detail = await self._call(
                "getTransaction",
                [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            )
            parsed = _parse_solana_transaction(signature, chain, observed, detail.get("result"))
            swaps.extend(parsed)
        self._cache.set(key, tuple(swaps))
        return swaps


def _parse_solana_transaction(
    signature: str, chain: str, observed: datetime, result: object
) -> list[ObservedSwap]:
    meta = _get(result, "meta")
    if not isinstance(meta, Mapping) or meta.get("err") is not None:
        return []
    transaction = _get(result, "transaction")
    instructions = _get(_get(transaction, "message"), "instructions")
    if not isinstance(instructions, list):
        return []
    pre = meta.get("preBalances")
    post = meta.get("postBalances")
    sol_delta = 0
    if isinstance(pre, list) and isinstance(post, list) and pre and post:
        sol_delta = int(post[0]) - int(pre[0])
    out: list[ObservedSwap] = []
    for instruction in instructions:
        parsed = _get(instruction, "parsed")
        info = _get(parsed, "info")
        if not isinstance(info, Mapping):
            continue
        mint = _text(_get(info, "mint"))
        token_amount = _get(info, "tokenAmount")
        raw_amount = _get(token_amount, "amount")
        decimals = _get(token_amount, "decimals", 0)
        if mint is None or raw_amount is None:
            continue
        amount = _dec(raw_amount)
        if amount is None:
            continue
        if isinstance(decimals, int) and decimals > 0:
            amount = amount / (Decimal(10) ** decimals)
        usd = _dec(_get(meta, "usdValue"))  # an extension, absent on the public wire
        out.append(
            ObservedSwap(
                source="solana-rpc",
                source_tx=signature,
                chain=chain,
                side="buy" if sol_delta < 0 else "sell",
                token_address=mint,
                token_symbol=_text(_get(info, "symbol")),
                amount_tokens=amount,
                amount_usd=usd,
                price_usd=None,
                observed_at=observed,
            )
        )
    return out


async def _http_get(path: str, params: dict[str, Any]) -> Any:
    url = "https://api.etherscan.io/v2/api"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url, params=params, headers={"User-Agent": "ezCHAIN/0.1"})
        response.raise_for_status()
        return response.json()


class EvmExplorerSource:
    """Explorer transactions for an EVM address, using the person's own key.

    A paid source: construction refuses an empty key, and the key is passed only
    to the provider call. The API resolves it from the encrypted provider-key
    store; this module never stores or logs it.
    """

    name = "evm-explorer"
    cost = "paid"

    def __init__(self, api_key: str, get: GetCall | None = None) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("an EVM explorer source requires the person's own API key")
        self._api_key = api_key
        self._get = get or _http_get

    async def fetch_swaps(
        self, *, chain: str, address: str, since: datetime, limit: int
    ) -> list[ObservedSwap]:
        params = {
            "chainid": 1,
            "module": "account",
            "action": "txlist",
            "address": address,
            "startblock": 0,
            "endblock": 99999999,
            "sort": "desc",
            "apikey": self._api_key,
        }
        payload = await self._get("api", params)
        rows = _get(payload, "result")
        if not isinstance(rows, list):
            return []
        out: list[ObservedSwap] = []
        for row in rows[:limit]:
            swap = _normalize_evm_row(chain, row, since)
            if swap is not None:
                out.append(swap)
        return out


def _normalize_evm_row(chain: str, row: object, since: datetime) -> ObservedSwap | None:
    tx = _text(_get(row, "hash"))
    observed = _stamp(_get(row, "timeStamp"))
    side = _get(row, "side")
    token = _text(_get(row, "contractAddress")) or _text(_get(row, "tokenAddress"))
    if tx is None or observed is None or observed < since or side not in ("buy", "sell"):
        return None
    if token is None:
        return None
    return ObservedSwap(
        source="evm-explorer",
        source_tx=tx,
        chain=chain,
        side=side,
        token_address=token,
        token_symbol=_text(_get(row, "tokenSymbol")),
        amount_tokens=_dec(_get(row, "tokenAmount")),
        amount_usd=_dec(_get(row, "usdValue")),
        price_usd=None,
        observed_at=observed,
    )


class FirehoseLaunchBuyersSource:
    """Launch buyers seen in pumpfun-firehose trade events.

    A free, read-only view over decoded firehose events: only labelled buys with
    a user and a signature become observations, and one signature is one swap.
    """

    name = "pumpfun-firehose"
    cost = "free"

    def __init__(self, events: Iterable[object] = ()) -> None:
        self._events = list(events)
        self._users: dict[str, str | None] = {}
        for raw in self._events:
            signature = _text(_get(raw, "signature"))
            if signature is not None and signature not in self._users:
                self._users[signature] = _text(_get(raw, "user"))

    def buyers(self) -> list[ObservedSwap]:
        seen: set[str] = set()
        out: list[ObservedSwap] = []
        for raw in self._events:
            if _get(raw, "kind") != "trade" or _get(raw, "direction") != "buy":
                continue
            signature = _text(_get(raw, "signature"))
            mint = _text(_get(raw, "mint"))
            user = _text(_get(raw, "user"))
            if signature is None or mint is None or user is None or signature in seen:
                continue
            seen.add(signature)
            out.append(
                ObservedSwap(
                    source="pumpfun-firehose",
                    source_tx=signature,
                    chain="solana",
                    side="buy",
                    token_address=mint,
                    token_symbol=_text(_get(raw, "symbol")),
                    amount_tokens=_dec(_get(raw, "sol")),
                    amount_usd=None,
                    price_usd=None,
                    observed_at=_stamp(_get(raw, "timestamp")) or datetime.now(UTC),
                )
            )
        return out

    async def fetch_swaps(
        self, *, chain: str, address: str, since: datetime, limit: int
    ) -> list[ObservedSwap]:
        return [
            swap
            for swap in self.buyers()
            if swap.observed_at >= since and self._users.get(swap.source_tx) == address
        ][:limit]


__all__ = [
    "EvmExplorerSource",
    "FirehoseLaunchBuyersSource",
    "ObservedSwap",
    "SolanaPublicRpcSource",
    "WalletActivitySource",
]
