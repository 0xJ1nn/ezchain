"""Live market browsing and persisted observations."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ezchain_adapters import (
    AdapterError,
    ChainCoverage,
    DexScreenerClient,
    GeckoTerminalClient,
    QuoteRouter,
    get_chain,
)
from ezchain_core import MarketRow

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
    MarketFiltersOut,
    MarketListOut,
    MarketOverviewOut,
    MarketPopulationOut,
    MarketRowOut,
    MarketSort,
    MarketSortSemantics,
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

CandleTimeframe = Literal["1m", "5m", "15m", "1h", "4h", "1d"]

# The adapter caps one /latest/dex/tokens response at 30 pairs and probes each chain once,
# so the whole registered population is bounded well below this. Asking for the whole
# population is what lets the API filter and page before the HTTP limit is applied.
BROWSE_DISCOVERY_ROWS = 1000


def _invalid_filter(message: str) -> None:
    raise HTTPException(
        status_code=422,
        detail={"code": "INVALID_FILTER", "message": message, "retryable": False},
    )


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _decimal_text(value: Decimal | None) -> str | None:
    """Plain decimal text, never scientific notation, so the URL round-trip is literal."""
    return None if value is None else format(value, "f")


def _apply_filters(
    rows: list[MarketRow],
    *,
    dex: str | None,
    min_liquidity_usd: Decimal | None,
    min_volume_24h_usd: Decimal | None,
    min_change_h24: Decimal | None,
    max_change_h24: Decimal | None,
    min_pair_age_minutes: int | None,
    max_pair_age_minutes: int | None,
    now: datetime,
) -> tuple[list[MarketRow], dict[str, int]]:
    """Filter rows without inventing values for unknowns.

    A row whose value for an active filter is ``None`` is excluded and counted in
    ``unknown_excluded``. The count is evaluated on the discovered population, before
    any page limit, so it explains why the total is smaller than the census.
    """
    unknown: dict[str, int] = {}
    kept: list[MarketRow] = []
    dex_casefold = None if dex is None else dex.casefold()
    for row in rows:
        unknown_fields: set[str] = set()
        if min_liquidity_usd is not None and row.liquidity_usd is None:
            unknown_fields.add("liquidity_usd")
        if min_volume_24h_usd is not None and row.volume_24h_usd is None:
            unknown_fields.add("volume_24h_usd")
        if (min_change_h24 is not None or max_change_h24 is not None) and (
            row.price_change_h24 is None
        ):
            unknown_fields.add("price_change_h24")
        if (min_pair_age_minutes is not None or max_pair_age_minutes is not None) and (
            row.pair_created_at is None
        ):
            unknown_fields.add("pair_created_at")
        if unknown_fields:
            for field in unknown_fields:
                unknown[field] = unknown.get(field, 0) + 1
            continue

        if min_liquidity_usd is not None and row.liquidity_usd is not None:
            if row.liquidity_usd < min_liquidity_usd:
                continue
        if min_volume_24h_usd is not None and row.volume_24h_usd is not None:
            if row.volume_24h_usd < min_volume_24h_usd:
                continue
        if min_change_h24 is not None and row.price_change_h24 is not None:
            if row.price_change_h24 < min_change_h24:
                continue
        if max_change_h24 is not None and row.price_change_h24 is not None:
            if row.price_change_h24 > max_change_h24:
                continue
        if row.pair_created_at is not None:
            age_minutes = max(0.0, (now - _aware(row.pair_created_at)).total_seconds() / 60.0)
            if min_pair_age_minutes is not None and age_minutes < min_pair_age_minutes:
                continue
            if max_pair_age_minutes is not None and age_minutes > max_pair_age_minutes:
                continue
        if dex_casefold is not None and row.dex.casefold() != dex_casefold:
            continue
        kept.append(row)
    return kept, unknown


def _sort_semantics(
    chain: str, q: str | None, coverage: list[ChainCoverage] | None
) -> MarketSortSemantics:
    """All-chain discovery is the adapter's per-chain round robin, not one global rank."""
    if chain == "all" and q is None and coverage is not None:
        return "per_chain_round_robin"
    return "global_sort"


def _population(
    *,
    rows: list[MarketRow],
    coverage: list[ChainCoverage] | None,
    total: int,
    returned: int,
    sort: MarketSort,
    semantics: MarketSortSemantics,
) -> MarketPopulationOut:
    if coverage is None:
        discovered = len(rows)
        truncated = False
        chains_scanned = None
        addresses_requested = None
    else:
        discovered = sum(item.returned for item in coverage)
        truncated = discovered > len(rows)
        chains_scanned = len(coverage)
        addresses_requested = sum(item.requested for item in coverage)
    return MarketPopulationOut(
        chains_scanned=chains_scanned,
        addresses_requested=addresses_requested,
        rows_discovered=discovered,
        rows_after_filters=total,
        rows_returned=returned,
        truncated=truncated,
        sort=sort,
        sort_semantics=semantics,
    )


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
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> StoredMarketListOut:
    """Latest persisted observation per token, ordered by liquidity (unknowns last).

    Same offset contract as the live browse: ``total`` is the persisted population and
    ``next_offset`` is present only while another page exists.
    """
    if chain != "all" and get_chain(chain) is None:
        raise HTTPException(
            status_code=404, detail={"code": "CHAIN_NOT_FOUND", "message": f"unknown chain {chain}"}
        )
    now = datetime.now(UTC)
    total = count_stored_snapshots(session, chain=chain)
    rows = stored_snapshots(session, chain=chain, limit=limit, offset=offset)
    returned = len(rows)
    has_more = offset + returned < total
    return StoredMarketListOut(
        rows=[_stored(row, now) for row in rows],
        as_of=now.isoformat(),
        offset=offset,
        limit=limit,
        total=total,
        has_more=has_more,
        next_offset=offset + returned if has_more else None,
    )


@router.get("/api/v1/markets", response_model=MarketListOut)
async def markets(
    chain: str = Query(default="all"),
    q: str | None = Query(default=None, max_length=80),
    sort: MarketSort = Query(default="trending"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    min_liquidity_usd: Decimal | None = Query(default=None, ge=0),
    min_volume_24h_usd: Decimal | None = Query(default=None, ge=0),
    min_change_h24: Decimal | None = Query(default=None),
    max_change_h24: Decimal | None = Query(default=None),
    min_pair_age_minutes: int | None = Query(default=None, ge=0),
    max_pair_age_minutes: int | None = Query(default=None, ge=0),
    dex: str | None = Query(default=None, max_length=80),
    adapter: DexScreenerClient = Depends(get_dexscreener),
) -> MarketListOut:
    """Browse the discovered market population with server-side filters and paging.

    Discovery returns the whole population (bounded by the provider's per-chain caps);
    filters and sorts are applied before ``offset``/``limit`` so ``total`` is the
    filtered count and paging cannot skip or duplicate a row within one snapshot.
    """
    if chain != "all" and get_chain(chain) is None:
        raise HTTPException(
            status_code=404, detail={"code": "CHAIN_NOT_FOUND", "message": f"unknown chain {chain}"}
        )
    if q is not None:
        q = q.strip() or None
    if dex is not None:
        dex = dex.strip() or None
    if (
        min_change_h24 is not None
        and max_change_h24 is not None
        and min_change_h24 > max_change_h24
    ):
        _invalid_filter("min_change_h24 must be less than or equal to max_change_h24")
    if (
        min_pair_age_minutes is not None
        and max_pair_age_minutes is not None
        and min_pair_age_minutes > max_pair_age_minutes
    ):
        _invalid_filter("min_pair_age_minutes must be less than or equal to max_pair_age_minutes")

    try:
        rows, source, coverage = await adapter.discover_markets(
            chain=chain, query=q, sort=sort, limit=BROWSE_DISCOVERY_ROWS
        )
    except AdapterError as exc:
        raise adapter_http_error(exc) from exc

    now = datetime.now(UTC)
    filtered, unknown = _apply_filters(
        rows,
        dex=dex,
        min_liquidity_usd=min_liquidity_usd,
        min_volume_24h_usd=min_volume_24h_usd,
        min_change_h24=min_change_h24,
        max_change_h24=max_change_h24,
        min_pair_age_minutes=min_pair_age_minutes,
        max_pair_age_minutes=max_pair_age_minutes,
        now=now,
    )
    total = len(filtered)
    page = filtered[offset : offset + limit]
    has_more = offset + len(page) < total
    next_offset = offset + len(page) if has_more else None
    semantics = _sort_semantics(chain, q, coverage)
    return MarketListOut(
        rows=[MarketRowOut.model_validate(row.to_dict()) for row in page],
        source=SourceOut.model_validate(source.to_dict()),
        stale=source.is_stale(60),
        coverage=None
        if coverage is None
        else [MarketCoverageOut.model_validate(item.to_dict()) for item in coverage],
        ordered_by="chain_round_robin" if semantics == "per_chain_round_robin" else "global_sort",
        sort=sort,
        sort_semantics=semantics,
        offset=offset,
        limit=limit,
        total=total,
        has_more=has_more,
        next_offset=next_offset,
        applied_filters=MarketFiltersOut(
            chain=chain,
            q=q,
            dex=dex,
            min_liquidity_usd=_decimal_text(min_liquidity_usd),
            min_volume_24h_usd=_decimal_text(min_volume_24h_usd),
            min_change_h24=_decimal_text(min_change_h24),
            max_change_h24=_decimal_text(max_change_h24),
            min_pair_age_minutes=min_pair_age_minutes,
            max_pair_age_minutes=max_pair_age_minutes,
            unknown_excluded=unknown,
        ),
        population=_population(
            rows=rows,
            coverage=coverage,
            total=total,
            returned=len(page),
            sort=sort,
            semantics=semantics,
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
    return QuoteOut.model_validate(result.to_dict())
