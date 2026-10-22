"""GeckoTerminal token metadata: the decimals a raw on-chain amount is scaled by.

Aggregator quote responses give raw integer amounts. Every one of them also needs the
token's decimals before the amount means anything, and the providers disagree about
whether they report it: Velora and LI.FI do, KyberSwap, Cetus and SQS do not. This client
is the single metadata fallback (``/networks/{net}/tokens/{address}`` →
``attributes.decimals``).

GeckoTerminal's public beta allows 30 requests/minute and returns 429 without rate-limit
headers, so the bucket is paced at 0.5 rps with a burst of 2. Decimals do not change, so
the response cache is an hour: one lookup per token per process-hour, not per quote.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from .errors import NotFound, UpstreamError
from .http import JsonApiClient

BASE = "https://api.geckoterminal.com/api/v2"
CACHE_TTL_SECONDS = 3600.0
MAX_TOKEN_DECIMALS = 36


def _path_segment(value: str) -> str:
    """Escape a token address; ``::`` in Move addresses stays readable."""
    return quote(value, safe=":")


class GeckoTerminalClient(JsonApiClient):
    label = "GeckoTerminal"
    base_url = BASE
    cache_ttl_seconds = CACHE_TTL_SECONDS
    rate_per_second = 0.5  # 30 requests/minute
    burst = 2
    not_found_statuses = frozenset({404})
    extra_headers = {"Accept": "application/json"}

    async def token_decimals(self, network: str, address: str) -> int:
        payload = await self._get(
            f"/networks/{_path_segment(network)}/tokens/{_path_segment(address)}"
        )
        attributes = _attributes(payload)
        raw = attributes.get("decimals")
        if isinstance(raw, bool) or raw is None:
            raise UpstreamError("GeckoTerminal returned malformed token decimals")
        try:
            decimals = int(str(raw))
        except (TypeError, ValueError):
            raise UpstreamError("GeckoTerminal returned malformed token decimals") from None
        if not 0 <= decimals <= MAX_TOKEN_DECIMALS:
            raise UpstreamError("GeckoTerminal returned malformed token decimals")
        return decimals


def _attributes(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise UpstreamError("GeckoTerminal returned an unexpected token payload")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise NotFound("GeckoTerminal does not index that token")
    attributes = data.get("attributes")
    if not isinstance(attributes, dict):
        raise UpstreamError("GeckoTerminal returned an unexpected token payload")
    return attributes
