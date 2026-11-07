"""Live market browsing and persisted observations."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ezchain_adapters import (
    AdapterError,
    DexScreenerClient,
    GeckoTerminalClient,
    QuoteRouter,
    get_chain,
)
from ezchain_core import MarketRow, preview_cost

from ..config import Settings, get_settings
from ..deps import (
    get_dexscreener,
    get_geckoterminal,
    get_market_monitor,
    get_quote_router,
    get_session,
)
from ..errors import adapter_http_error, candles_http_error
from ..models import MarketSnapshotRow
from ..schemas import (
    CandlesOut,
    MarketCoverageOut,
    MarketListOut,
    MarketOverviewOut,
    MarketPopulationOut,
    MarketRowOut,
    QuoteOut,
    SourceOut,
    StoredMarketListOut,
    StoredSnapshotOut,
    TokenOut,
)
from ..services.candles import get_candles
from ..services.market_monitor import (
    MarketMonitor,
    build_overview,
    count_stored_snapshots,
    stored_snapshots,
)

router = APIRouter(tags=["markets"])

BROWSE_DISCOVERY_ROWS = 1000


def _invalid_cursor(message: str) -> NoReturn:
    raise HTTPException(
        status_code=400,
        detail={"code": "INVALID_CURSOR", "message": message, "retryable": False},
    )


def _encode_cursor(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(token: str) -> dict[str, object]:
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        _invalid_cursor("cursor is not a valid ezchain cursor")
        raise AssertionError from exc
    if not isinstance(data, dict) or data.get("v") != 1:
        _invalid_cursor("cursor version is not supported")
    return data


def _market_anchor(row: MarketRow) -> str:
    return f"{row.chain}:{row.pair_address}"


def _stored_anchor(row: MarketSnapshotRow) -> str:
    return str(row.id)


CandleTimeframe = Literal["1m", "5m", "15m", "1h", "4h", "1d"]


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


@router.get("/api/v1/markets/stored", response_model=StoredMarketListOut)
def market_stored(
    chain: str = Query(default="all"),
    cursor: str | None = Query(default=None),
    page_cursor: str | None = Query(default=None, alias="page_cursor"),
    after: str | None = Query(default=None, alias="after"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> StoredMarketListOut:
    """Latest persisted observation per token, ordered by liquidity (unknowns last).

    Same paging contract as the live browse: ``total`` is the persisted population and
    ``next_cursor``/``next_offset`` are present only while another page exists.
    """
    if chain != "all" and get_chain(chain) is None:
        raise HTTPException(
            status_code=404, detail={"code": "CHAIN_NOT_FOUND", "message": f"unknown chain {chain}"}
        )
    effective_cursor = cursor or page_cursor or after
    now = datetime.now(UTC)
    total = count_stored_snapshots(session, chain=chain)
    if effective_cursor is not None:
        data = _decode_cursor(effective_cursor)
        if data.get("kind") != "stored" or data.get("chain") != chain:
            _invalid_cursor("cursor does not match this request")
        anchor_id = data.get("id")
        if not isinstance(anchor_id, str):
            _invalid_cursor("cursor is missing its anchor")
        all_rows = stored_snapshots(session, chain=chain, limit=100000, offset=0)
        anchor_index = next(
            (index for index, row in enumerate(all_rows) if str(row.id) == anchor_id), None
        )
        if anchor_index is None:
            _invalid_cursor("cursor refers to a row that no longer exists")
        start = anchor_index + 1
        page_rows = all_rows[start : start + limit]
    else:
        start = offset
        page_rows = stored_snapshots(session, chain=chain, limit=limit, offset=offset)
    returned = len(page_rows)
    has_more = start + returned < total
    next_cursor = None
    if has_more and page_rows:
        next_cursor = _encode_cursor(
            {"v": 1, "kind": "stored", "chain": chain, "id": _stored_anchor(page_rows[-1])}
        )
    # next_cursor should point after last returned; already encoded with last row anchor.
    return StoredMarketListOut(
        rows=[_stored(row, now) for row in page_rows],
        as_of=now.isoformat(),
        offset=start,
        limit=limit,
        total=total,
        has_more=has_more,
        next_offset=start + returned if has_more else None,
        next_cursor=next_cursor,
    )


@router.get("/api/v1/markets", response_model=MarketListOut)
async def markets(
    chain: str = Query(default="all"),
    q: str | None = Query(default=None, max_length=80),
    sort: Literal["trending", "liquidity", "new"] = Query(default="trending"),
    cursor: str | None = Query(default=None),
    page_cursor: str | None = Query(default=None, alias="page_cursor"),
    after: str | None = Query(default=None, alias="after"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    adapter: DexScreenerClient = Depends(get_dexscreener),
) -> MarketListOut:
    if chain != "all" and get_chain(chain) is None:
        raise HTTPException(
            status_code=404, detail={"code": "CHAIN_NOT_FOUND", "message": f"unknown chain {chain}"}
        )
    try:
        rows, source, coverage = await adapter.discover_markets(
            chain=chain, query=q, sort=sort, limit=BROWSE_DISCOVERY_ROWS
        )
    except AdapterError as exc:
        raise adapter_http_error(exc) from exc

    effective_cursor = cursor or page_cursor or after
    total = len(rows)
    if effective_cursor is not None:
        data = _decode_cursor(effective_cursor)
        if (
            data.get("kind") != "markets"
            or data.get("chain") != chain
            or data.get("sort") != sort
            or data.get("q") != q
        ):
            _invalid_cursor("cursor does not match this request")
        anchor_id = data.get("id")
        if not isinstance(anchor_id, str):
            _invalid_cursor("cursor is missing its anchor")
        anchor_index = next(
            (index for index, row in enumerate(rows) if _market_anchor(row) == anchor_id), None
        )
        if anchor_index is None:
            _invalid_cursor("cursor refers to a row that no longer exists")
        start = anchor_index + 1
    else:
        start = offset
    if start < 0:
        start = 0
    if start > total:
        start = total
    page = rows[start : start + limit]
    has_more = start + len(page) < total
    semantics = (
        "chain_round_robin"
        if coverage is not None and q is None and chain == "all"
        else "global_sort"
    )
    semantics_value = "per_chain_round_robin" if semantics == "chain_round_robin" else "global_sort"
    ordered_by = "chain_round_robin" if semantics == "chain_round_robin" else "global_sort"

    discovered = len(rows)
    truncated = False
    chains_scanned = None
    addresses_requested = None
    if coverage is not None:
        discovered = sum(item.returned for item in coverage)
        truncated = discovered > len(rows)
        chains_scanned = len(coverage)
        addresses_requested = sum(item.requested for item in coverage)

    next_cursor = None
    if has_more and page:
        next_cursor = _encode_cursor(
            {
                "v": 1,
                "kind": "markets",
                "chain": chain,
                "sort": sort,
                "q": q,
                "id": _market_anchor(page[-1]),
            }
        )

    return MarketListOut(
        rows=[MarketRowOut.model_validate(row.to_dict()) for row in page],
        source=SourceOut.model_validate(source.to_dict()),
        stale=source.is_stale(60),
        coverage=None
        if coverage is None
        else [MarketCoverageOut.model_validate(item.to_dict()) for item in coverage],
        ordered_by=ordered_by,  # type: ignore[arg-type]
        sort=sort,
        sort_semantics=semantics_value,  # type: ignore[arg-type]
        offset=start,
        limit=limit,
        total=total,
        has_more=has_more,
        next_offset=start + len(page) if has_more else None,
        next_cursor=next_cursor,
        truncated=truncated,
        population=MarketPopulationOut(
            chains_scanned=chains_scanned,
            addresses_requested=addresses_requested,
            rows_discovered=discovered,
            rows_after_filters=total,
            rows_returned=len(page),
            truncated=truncated,
            sort=sort,
            sort_semantics=semantics_value,  # type: ignore[arg-type]
        ),
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


@router.get("/api/v1/tokens/{chain}/{address}/candles", response_model=CandlesOut)
async def token_candles(
    chain: str,
    address: str,
    timeframe: CandleTimeframe = Query(default="5m"),
    limit: int = Query(default=300, ge=1, le=1000),
    session: Session = Depends(get_session),
    snapshot_source: DexScreenerClient = Depends(get_dexscreener),
    candle_source: GeckoTerminalClient = Depends(get_geckoterminal),
    settings: Settings = Depends(get_settings),
) -> CandlesOut:
    """Closed OHLCV history for one token, oldest bar first.

    Read-only and unauthenticated like every other GET. The series is served from
    ``market_candles`` when it is current; only a missing range reaches the provider.
    """
    if get_chain(chain) is None:
        raise HTTPException(
            status_code=404, detail={"code": "CHAIN_NOT_FOUND", "message": f"unknown chain {chain}"}
        )
    try:
        series = await get_candles(
            session,
            candle_source,
            snapshot_source,
            chain,
            address,
            timeframe,
            limit,
            datetime.now(UTC),
            data_mode=settings.data_mode,
        )
    except AdapterError as exc:
        raise candles_http_error(exc) from exc
    return CandlesOut.model_validate(series.to_dict())


@router.get("/api/v1/quotes", response_model=QuoteOut)
async def quote(
    chain: str,
    token: str,
    side: Literal["buy", "sell"],
    amount_usd: Decimal = Query(gt=0),
    quantity: Decimal | None = Query(default=None, gt=0),
    quotes: QuoteRouter = Depends(get_quote_router),
    settings: Settings = Depends(get_settings),
) -> QuoteOut:
    if get_chain(chain) is None:
        raise HTTPException(
            status_code=404, detail={"code": "CHAIN_NOT_FOUND", "message": f"unknown chain {chain}"}
        )
    try:
        result = await quotes.quote(
            chain=chain,
            token_address=token,
            side=side,
            amount_usd=amount_usd,
            quantity=quantity,
        )
    except AdapterError as exc:
        raise adapter_http_error(exc) from exc
    # The pre-trade sheet, via the one cost model: this is the same preview the journal
    # records on the intent when the order fires, so the two agree by construction.
    notional = result.amount_in if side == "buy" else result.amount_out
    payload = result.to_dict()
    payload["cost_preview"] = preview_cost(
        kind=result.kind,
        notional_usd=notional,
        venue_fee_usd=result.fee_usd,
        price_impact_pct=result.price_impact_pct,
        amount_out=result.amount_out,
        fee_bps=settings.paper_fee_bps,
        source=result.source,
    ).to_dict()
    return QuoteOut.model_validate(payload)
