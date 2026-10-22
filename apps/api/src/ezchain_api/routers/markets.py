"""Live market browsing and persisted observations."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ezchain_adapters import AdapterError, DexScreenerClient, JupiterClient, get_chain

from ..config import Settings, get_settings
from ..deps import get_dexscreener, get_jupiter, get_market_monitor, get_session
from ..errors import adapter_http_error
from ..models import MarketSnapshotRow
from ..schemas import (
    MarketCoverageOut,
    MarketListOut,
    MarketOverviewOut,
    MarketRowOut,
    QuoteOut,
    SourceOut,
    StoredSnapshotOut,
    TokenOut,
)
from ..services.market_monitor import MarketMonitor, build_overview, stored_snapshots
from ..services.quotes import get_quote

router = APIRouter(tags=["markets"])


@router.get("/api/v1/markets/overview", response_model=MarketOverviewOut)
def market_overview(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    monitor: MarketMonitor | None = Depends(get_market_monitor),
) -> MarketOverviewOut:
    """What this process can see right now, one row per registered chain."""
    return MarketOverviewOut.model_validate(build_overview(session, monitor, settings))


def _stored(row: MarketSnapshotRow, now: datetime) -> StoredSnapshotOut:
    observed = (
        row.observed_at.astimezone(UTC)
        if row.observed_at.tzinfo is not None
        else row.observed_at.replace(tzinfo=UTC)
    )
    return StoredSnapshotOut(
        chain=row.chain,
        token_address=row.token_address,
        symbol=row.symbol,
        name=row.name,
        price_usd=None if row.price_usd is None else str(row.price_usd),
        liquidity_usd=None if row.liquidity_usd is None else str(row.liquidity_usd),
        volume_24h_usd=None if row.volume_24h_usd is None else str(row.volume_24h_usd),
        price_change_m5=None if row.price_change_m5 is None else str(row.price_change_m5),
        price_change_h1=None if row.price_change_h1 is None else str(row.price_change_h1),
        price_change_h6=None if row.price_change_h6 is None else str(row.price_change_h6),
        price_change_h24=None if row.price_change_h24 is None else str(row.price_change_h24),
        source=row.source,
        observed_at=observed.isoformat(),
        age_seconds=int(max(0.0, (now - observed).total_seconds())),
    )


@router.get("/api/v1/markets/stored", response_model=list[StoredSnapshotOut])
def market_stored(
    chain: str = Query(default="all"),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> list[StoredSnapshotOut]:
    """Latest persisted observation per token, ordered by liquidity (unknowns last)."""
    if chain != "all" and get_chain(chain) is None:
        raise HTTPException(
            status_code=404, detail={"code": "CHAIN_NOT_FOUND", "message": f"unknown chain {chain}"}
        )
    now = datetime.now(UTC)
    return [_stored(row, now) for row in stored_snapshots(session, chain=chain, limit=limit)]


@router.get("/api/v1/markets", response_model=MarketListOut)
async def markets(
    chain: str = Query(default="all"),
    q: str | None = Query(default=None, max_length=80),
    sort: Literal["trending", "liquidity", "new"] = Query(default="trending"),
    limit: int = Query(default=50, ge=1, le=100),
    adapter: DexScreenerClient = Depends(get_dexscreener),
) -> MarketListOut:
    if chain != "all" and get_chain(chain) is None:
        raise HTTPException(
            status_code=404, detail={"code": "CHAIN_NOT_FOUND", "message": f"unknown chain {chain}"}
        )
    try:
        rows, source, coverage = await adapter.discover_markets(
            chain=chain, query=q, sort=sort, limit=limit
        )
    except AdapterError as exc:
        raise adapter_http_error(exc) from exc
    return MarketListOut(
        rows=[MarketRowOut.model_validate(row.to_dict()) for row in rows],
        source=SourceOut.model_validate(source.to_dict()),
        stale=source.is_stale(60),
        coverage=None
        if coverage is None
        else [MarketCoverageOut.model_validate(item.to_dict()) for item in coverage],
    )


@router.get("/api/v1/tokens/{chain}/{address}", response_model=TokenOut)
async def token(
    chain: str,
    address: str,
    adapter: DexScreenerClient = Depends(get_dexscreener),
) -> TokenOut:
    if get_chain(chain) is None:
        raise HTTPException(
            status_code=404, detail={"code": "CHAIN_NOT_FOUND", "message": f"unknown chain {chain}"}
        )
    try:
        snapshot = await adapter.get_token(chain, address)
    except AdapterError as exc:
        raise adapter_http_error(exc) from exc
    return TokenOut.model_validate(snapshot.to_dict())


@router.get("/api/v1/quotes", response_model=QuoteOut)
async def quote(
    chain: str,
    token: str,
    side: Literal["buy", "sell"],
    amount_usd: Decimal = Query(gt=0),
    quantity: Decimal | None = Query(default=None, gt=0),
    dexscreener: DexScreenerClient = Depends(get_dexscreener),
    jupiter: JupiterClient = Depends(get_jupiter),
    settings: Settings = Depends(get_settings),
) -> QuoteOut:
    if get_chain(chain) is None:
        raise HTTPException(
            status_code=404, detail={"code": "CHAIN_NOT_FOUND", "message": f"unknown chain {chain}"}
        )
    try:
        result = await get_quote(
            chain=chain,
            token_address=token,
            side=side,
            amount_usd=amount_usd,
            quantity=quantity,
            dexscreener=dexscreener,
            jupiter=jupiter,
            settings=settings,
        )
    except AdapterError as exc:
        raise adapter_http_error(exc) from exc
    return QuoteOut.model_validate(result.to_dict())
