"""Per-token metadata read.

One token, one answer. Each number is an observation: value, unit, source and the time
the source answered. Nothing measured is ``null`` with a reason, never a zero; a source
outage still returns the fields that are known. The endpoint is a read, so it is open in
a self-hosted workspace and requires the ``read`` scope when hosted reads are gated.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ezchain_adapters import get_chain
from ezchain_adapters.metadata import DexScreenerMetadataProvider

from ..services.metadata import MetadataService, TokenMetadata
from ..services.metadata.models import Check, LPState, Observation, WindowMetrics

router = APIRouter(tags=["metadata"])


def _metadata_service(request: Request) -> MetadataService:
    """One service per process, sharing the DexScreener client's cache and pacing."""
    existing = getattr(request.app.state, "metadata_service", None)
    if existing is None:
        from ..deps import get_dexscreener

        existing = MetadataService(DexScreenerMetadataProvider(get_dexscreener(request)))
        request.app.state.metadata_service = existing
    service: MetadataService = existing
    return service


def _utc(value: datetime) -> str:
    return (
        value.astimezone(UTC).isoformat() if value.tzinfo else value.replace(tzinfo=UTC).isoformat()
    )


def _text(value: Decimal | int | bool | str | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return _utc(value)
    return str(value)


class ObservationOut(BaseModel):
    value: str | None
    known: bool
    unit: str
    source: str | None
    observed_at: str | None
    reason: str | None


class LPStateOut(BaseModel):
    status: str
    burned_pct: ObservationOut
    locked_pct: ObservationOut
    lock_duration_seconds: ObservationOut
    source: str | None
    observed_at: str | None


class WindowOut(BaseModel):
    applicable: bool
    price_change_pct: ObservationOut
    buys: ObservationOut
    sells: ObservationOut
    buy_volume_usd: ObservationOut
    sell_volume_usd: ObservationOut
    buying_makers: ObservationOut
    selling_makers: ObservationOut
    bsr: ObservationOut


class CheckOut(BaseModel):
    status: str
    observation: ObservationOut
    reason: str | None


class TokenMetadataOut(BaseModel):
    chain: str
    token_address: str
    as_of: str
    market_cap_usd: ObservationOut
    fdv_usd: ObservationOut
    liquidity_usd: ObservationOut
    liquidity_change_pct: ObservationOut
    lp: LPStateOut
    holders: ObservationOut
    holders_change_h1_pct: ObservationOut
    top_10_pct: ObservationOut
    dev_holdings_pct: ObservationOut
    pair_age_seconds: ObservationOut
    windows: dict[str, WindowOut]
    checks: dict[str, CheckOut]
    warnings: list[str]


def _observation(value: Observation) -> ObservationOut:
    return ObservationOut(
        value=_text(value.value),
        known=value.known,
        unit=value.unit,
        source=value.source,
        observed_at=_text(value.observed_at),
        reason=value.reason,
    )


def _lp(value: LPState) -> LPStateOut:
    return LPStateOut(
        status=value.status,
        burned_pct=_observation(value.burned_pct),
        locked_pct=_observation(value.locked_pct),
        lock_duration_seconds=_observation(value.lock_duration_seconds),
        source=value.source,
        observed_at=_text(value.observed_at),
    )


def _window(value: WindowMetrics) -> WindowOut:
    return WindowOut(
        applicable=value.applicable,
        price_change_pct=_observation(value.price_change_pct),
        buys=_observation(value.buys),
        sells=_observation(value.sells),
        buy_volume_usd=_observation(value.buy_volume_usd),
        sell_volume_usd=_observation(value.sell_volume_usd),
        buying_makers=_observation(value.buying_makers),
        selling_makers=_observation(value.selling_makers),
        bsr=_observation(value.bsr),
    )


def _check(value: Check) -> CheckOut:
    return CheckOut(
        status=value.status,
        observation=_observation(value.observation),
        reason=value.reason,
    )


def _out(value: TokenMetadata) -> TokenMetadataOut:
    return TokenMetadataOut(
        chain=value.chain,
        token_address=value.address,
        as_of=_utc(value.as_of),
        market_cap_usd=_observation(value.market_cap_usd),
        fdv_usd=_observation(value.fdv_usd),
        liquidity_usd=_observation(value.liquidity_usd),
        liquidity_change_pct=_observation(value.liquidity_change_pct),
        lp=_lp(value.lp),
        holders=_observation(value.holders),
        holders_change_h1_pct=_observation(value.holders_change_h1_pct),
        top_10_pct=_observation(value.top_10_pct),
        dev_holdings_pct=_observation(value.dev_holdings_pct),
        pair_age_seconds=_observation(value.pair_age_seconds),
        windows={name: _window(window) for name, window in value.windows.items()},
        checks={name: _check(check) for name, check in value.checks.items()},
        warnings=list(value.warnings),
    )


@router.get("/api/v1/tokens/{chain}/{address}/metadata", response_model=TokenMetadataOut)
async def token_metadata(
    chain: str,
    address: str,
    service: MetadataService = Depends(_metadata_service),
) -> TokenMetadataOut:
    """Market, liquidity, holders, activity and safety evidence for one token."""
    if not address or get_chain(chain) is None:
        detail: dict[str, Any] = {
            "code": "CHAIN_NOT_FOUND",
            "message": f"unknown chain {chain}",
            "retryable": False,
        }
        raise HTTPException(status_code=404, detail=detail)
    return _out(await service.get(chain, address))
