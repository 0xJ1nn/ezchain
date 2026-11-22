"""Per-token metadata reads with a short bounded cache.

The fetcher is injected so the API can keep the HTTP sources in the adapter layer and the
tests can supply recorded observations. One failure in a source is the adapter's concern;
if a fetcher still raises, the service turns it into an empty observation with a reason
rather than an exception, so the read model fail-closes with nulls instead of a guessed
verdict.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from ezchain_adapters.cache import MISSING, BoundedTTLCache
from ezchain_adapters.errors import AdapterError
from ezchain_adapters.metadata import TokenMetadataObservation

from .build import build_response

CACHE_TTL_SECONDS = 30.0
CACHE_MAX_ENTRIES = 512


class MetadataFetcher(Protocol):
    async def fetch(self, chain: str, address: str) -> TokenMetadataObservation: ...


class MetadataService:
    def __init__(
        self,
        *,
        fetcher: MetadataFetcher,
        clock: Callable[[], datetime] | None = None,
        ttl_seconds: float = CACHE_TTL_SECONDS,
        observation_type: Any = None,
    ) -> None:
        self._fetcher = fetcher
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cache = BoundedTTLCache(max_entries=CACHE_MAX_ENTRIES, ttl_seconds=ttl_seconds)
        # ``observation_type`` is accepted so callers can state the concrete observation
        # they recorded; the service itself only depends on the fetcher's return shape.
        self._observation_type = observation_type or TokenMetadataObservation

    async def read(self, chain: str, address: str) -> dict[str, Any]:
        key = f"{chain.strip().lower()}:{address.strip().lower()}"
        cached = self._cache.get(key)
        if cached is not MISSING:
            return cast(dict[str, Any], cached)
        try:
            observation = await self._fetcher.fetch(chain, address)
        except AdapterError as exc:
            observation = self._observation_type(
                chain=chain,
                address=address,
                degraded=(f"UNAVAILABLE:{exc.code}",),
            )
        except Exception:
            observation = self._observation_type(
                chain=chain,
                address=address,
                degraded=("UNAVAILABLE:FETCHER_ERROR",),
            )
        result = build_response(observation)
        self._cache.set(key, result)
        return result
