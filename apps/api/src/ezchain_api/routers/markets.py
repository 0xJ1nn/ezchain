"""Live market browsing."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from ezchain_adapters import AdapterError, DexScreenerClient, JupiterClient, get_chain

from ..config import Settings, get_settings
from ..deps import get_dexscreener, get_jupiter
from ..errors import adapter_http_error
from ..schemas import MarketListOut, MarketRowOut, QuoteOut, SourceOut, TokenOut
from ..services.quotes import get_quote

router = APIRouter(tags=["markets"])


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
        rows, source = await adapter.list_markets(chain=chain, query=q, limit=100)
    except AdapterError as exc:
        raise adapter_http_error(exc) from exc
    if sort == "liquidity":
        rows = sorted(rows, key=lambda r: r.liquidity_usd, reverse=True)
    elif sort == "new":
        rows = sorted(rows, key=lambda r: r.pair_created_at or r.source.observed_at, reverse=True)
    else:
        rows = sorted(rows, key=lambda r: r.volume_24h_usd, reverse=True)
    rows = rows[:limit]
    return MarketListOut(
        rows=[MarketRowOut.model_validate(row.to_dict()) for row in rows],
        source=SourceOut.model_validate(source.to_dict()),
        stale=source.is_stale(60),
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
