"""The token metadata router: one read per token, with provenance and fail-closed checks.

The response is a read model, not a verdict. Every value is wrapped with its unit, a
``known`` flag, the source that was asked and when that source answered. Unknown stays
null, a window longer than the pair's age is null, and a source outage returns the values
that are known together with a reason code. Nothing in this module calls a provider
directly: the adapter fetches server-side, and a Birdeye enrichment is only attempted
when a user credential has been supplied to that adapter.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ezchain_adapters.metadata import TokenMetadataClient

from ..schemas import SourceOut
from ..services.metadata.service import MetadataFetcher, MetadataService

router = APIRouter(tags=["metadata"])


class MetricOut(BaseModel):
    """One displayed value with the source and observation time behind it."""

    value: str | bool | None = None
    known: bool = False
    unit: str
    source: SourceOut | None = None
    observed_at: str | None = None


class CheckOut(BaseModel):
    """A safety verdict. ``unknown`` is a first-class status, never a false pass."""

    status: Literal["pass", "fail", "unknown"] = "unknown"
    value: str | bool | None = None
    reason: str | None = None
    source: SourceOut | None = None
    observed_at: str | None = None


class WindowOut(BaseModel):
    price_change_pct: MetricOut
    buys: MetricOut
    sells: MetricOut
    buy_volume_usd: MetricOut
    sell_volume_usd: MetricOut
    buying_makers: MetricOut
    selling_makers: MetricOut
    bsr: MetricOut


class MarketOut(BaseModel):
    price_usd: MetricOut
    market_cap_usd: MetricOut
    fdv_usd: MetricOut
    liquidity_usd: MetricOut
    liquidity_change_pct: MetricOut


class LiquidityOut(BaseModel):
    state: MetricOut
    lp_burned_pct: MetricOut
    lp_locked_pct: MetricOut
    lp_lock_duration_seconds: MetricOut


class HoldersOut(BaseModel):
    count: MetricOut
    change_h1_pct: MetricOut
    top_10_pct: MetricOut
    dev_holdings_pct: MetricOut


class SafetyOut(BaseModel):
    checks: dict[str, CheckOut]
    buy_tax_pct: MetricOut
    sell_tax_pct: MetricOut


class TokenMetadataOut(BaseModel):
    chain: str
    address: str
    symbol: MetricOut
    name: MetricOut
    market: MarketOut
    liquidity: LiquidityOut
    holders: HoldersOut
    pair_age_seconds: MetricOut
    windows: dict[str, WindowOut]
    safety: SafetyOut
    degraded: list[str] = Field(default_factory=list)


def get_metadata_fetcher(request: Request) -> MetadataFetcher:
    """The process fetcher, or a keyless one for an embedder that did not configure it.

    No credential is read from a browser request. A deployment that wants Birdeye
    enrichment supplies a client with a user-provided key on the application state; a
    process without one keeps the free-source path.
    """
    configured = getattr(request.app.state, "metadata_fetcher", None)
    return configured if configured is not None else TokenMetadataClient()


def get_metadata_service(request: Request) -> MetadataService:
    """One service per application, so the short per-token cache survives across reads."""
    service = getattr(request.app.state, "metadata_service", None)
    if service is None:
        service = MetadataService(fetcher=get_metadata_fetcher(request))
        request.app.state.metadata_service = service
    return service


@router.get("/api/tokens/{chain}/{address}/metadata", response_model=TokenMetadataOut)
async def token_metadata(
    chain: str,
    address: str,
    service: MetadataService = Depends(get_metadata_service),
) -> TokenMetadataOut:
    """Market, liquidity, holders, per-window activity and safety checks for one token.

    The response never claims more than the sources answered. A missing field is null
    with ``known: false``; a window older than the pair is null with a reason under
    ``degraded``; and a failed source contributes a reason instead of invented values.
    """
    result: dict[str, Any] = await service.read(chain, address)
    return TokenMetadataOut.model_validate(result)
