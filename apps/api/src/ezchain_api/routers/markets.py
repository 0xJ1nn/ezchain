"""Live market browsing and persisted observations."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ezchain_adapters import (
    AdapterError,
    DexScreenerClient,
    GeckoTerminalClient,
    QuoteRouter,
    get_chain,
)
from ezchain_core import (
    DEFAULT_STALENESS_SECONDS,
    MarketRow,
    TokenSnapshot,
    preview_cost,
)

from ..config import Settings, get_settings
from ..deps import (
    get_dexscreener,
    get_geckoterminal,
    get_market_monitor,
    get_quote_router,
    get_session,
)
from ..errors import adapter_http_error, candles_http_error, refuse_unknown_query
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
    stored_snapshots,
)
from ..services.market_reads import provenance, source_ref

router = APIRouter(tags=["markets"])

BROWSE_DISCOVERY_ROWS = 1000

# A filter is echoed back in ``applied_filters``. Without a bound, Decimal accepts
# ``1e1000000`` and ``format(value, "f")`` expands it to a megabyte of digits before any
# provider work happens. The supported range covers every realistic USD/percent filter
# and caps both the magnitude and the number of significant digits.
FILTER_MAX_ABS = Decimal("1e18")
FILTER_MIN_ABS = Decimal("1e-18")
FILTER_MAX_DIGITS = 30


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


def _invalid_filter(message: str) -> NoReturn:
    raise HTTPException(
        status_code=422,
        detail={"code": "INVALID_FILTER", "message": message, "retryable": False},
    )


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _validate_decimal_filter(name: str, value: Decimal | None, *, non_negative: bool) -> None:
    if value is None:
        return
    if not value.is_finite():
        _invalid_filter(f"{name} must be finite")
    if value.is_zero():
        return
    if non_negative and value.as_tuple().sign:
        _invalid_filter(f"{name} must be greater than or equal to zero")
    adjusted = value.adjusted()
    if adjusted > 18 or (adjusted == 18 and value > FILTER_MAX_ABS):
        _invalid_filter(f"{name} must not exceed {FILTER_MAX_ABS:f}")
    if adjusted < -18:
        _invalid_filter(f"{name} must be zero or at least {FILTER_MIN_ABS:f}")
    if len(value.as_tuple().digits) > FILTER_MAX_DIGITS:
        _invalid_filter(f"{name} must have at most {FILTER_MAX_DIGITS} significant digits")


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if value.is_zero():
        return "0"
    if not value.is_finite():
        return str(value)
    digits = value.as_tuple().digits
    adjusted = value.adjusted()
    if (
        len(digits) > FILTER_MAX_DIGITS
        or adjusted > 18
        or adjusted < -18
        or (adjusted == 18 and (value > FILTER_MAX_ABS or value < -FILTER_MAX_ABS))
    ):
        return str(value)
    return format(value, "f")


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


def _sort_semantics(chain: str, q: str | None, coverage: Any) -> str:
    if chain == "all" and q is None and coverage is not None:
        return "per_chain_round_robin"
    return "global_sort"


def _population(
    *,
    rows: list[MarketRow],
    coverage: Any,
    total: int,
    returned: int,
    sort: MarketSort,
    semantics: str,
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
        sort_semantics=semantics,  # type: ignore[arg-type]
    )


def _live_row(row: MarketRow, now: datetime) -> MarketRowOut:
    """One discovery row plus the contract's own source, age and verdict at read time."""
    freshness = provenance(row.source, now=now)
    body = row.to_dict()
    return MarketRowOut.model_validate(
        {
            **body,
            "source": SourceOut.model_validate(row.source.to_dict()),
            "age_seconds": freshness.age_seconds,
            "stale": freshness.stale,
        }
    )


def _token_out(snapshot: TokenSnapshot, now: datetime) -> TokenOut:
    """The token header, with freshness derived here instead of trusting the adapter.

    The snapshot's ``stale`` flag is what the adapter believed when it answered. The API
    reports the contract's verdict at this read's clock, so an old observation cannot be
    presented as fresh. ``TokenOut.stale`` is a boolean, so an unknown verdict behind a
    known source (a timestamp beyond the tolerated future skew, ADR-0036) reports stale:
    it must not read as fresh. A token with no source at all keeps the documented ``False``.
    """
    origin = snapshot.source
    freshness = provenance(origin, now=now)
    stale = bool(freshness.stale) if freshness.stale is not None else origin is not None
    return TokenOut.model_validate(
        {
            **snapshot.to_dict(),
            "stale": stale,
            "age_seconds": freshness.age_seconds,
        }
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
    origin = source_ref(row.source, observed)
    freshness = provenance(origin, now=now)
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
        source=None if origin is None else SourceOut.model_validate(origin.to_dict()),
        observed_at=observed.isoformat(),
        age_seconds=freshness.age_seconds,
        stale=freshness.stale,
        pair_address=row.pair_address,
        dex=row.dex,
        base_token_address=row.base_token_address,
        quote_token_address=row.quote_token_address,
        pair_created_at=(
            None if row.pair_created_at is None else _aware(row.pair_created_at).isoformat()
        ),
        url=row.url,
        pair_age_seconds=None
        if row.pair_created_at is None
        else int(max(0.0, (observed - _aware(row.pair_created_at)).total_seconds())),
    )


def _apply_stored_filters(
    rows: list[MarketSnapshotRow],
    *,
    dex: str | None,
    min_liquidity_usd: Decimal | None,
    min_volume_24h_usd: Decimal | None,
    min_change_h24: Decimal | None,
    max_change_h24: Decimal | None,
    min_pair_age_minutes: int | None,
    max_pair_age_minutes: int | None,
    now: datetime,
) -> tuple[list[MarketSnapshotRow], dict[str, int]]:
    unknown: dict[str, int] = {}
    kept: list[MarketSnapshotRow] = []
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
        if row.pair_created_at is not None and (
            min_pair_age_minutes is not None or max_pair_age_minutes is not None
        ):
            age_minutes = max(0.0, (now - _aware(row.pair_created_at)).total_seconds() / 60.0)
            if min_pair_age_minutes is not None and age_minutes < min_pair_age_minutes:
                continue
            if max_pair_age_minutes is not None and age_minutes > max_pair_age_minutes:
                continue
        if dex is not None:
            if row.dex is None or row.dex.casefold() != dex.casefold():
                continue
        kept.append(row)
    return kept, unknown


def _stored_filters_payload(
    chain: str,
    q: str | None,
    dex: str | None,
    min_liquidity_usd: Decimal | None,
    min_volume_24h_usd: Decimal | None,
    min_change_h24: Decimal | None,
    max_change_h24: Decimal | None,
    min_pair_age_minutes: int | None,
    max_pair_age_minutes: int | None,
    unknown: dict[str, int],
) -> MarketFiltersOut:
    return MarketFiltersOut(
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
    )


def _market_filters_payload(
    chain: str,
    q: str | None,
    dex: str | None,
    min_liquidity_usd: Decimal | None,
    min_volume_24h_usd: Decimal | None,
    min_change_h24: Decimal | None,
    max_change_h24: Decimal | None,
    min_pair_age_minutes: int | None,
    max_pair_age_minutes: int | None,
    unknown: dict[str, int],
) -> MarketFiltersOut:
    return _stored_filters_payload(
        chain,
        q,
        dex,
        min_liquidity_usd,
        min_volume_24h_usd,
        min_change_h24,
        max_change_h24,
        min_pair_age_minutes,
        max_pair_age_minutes,
        unknown,
    )


def _cursor_market_matches(
    data: dict[str, object],
    *,
    chain: str,
    sort: MarketSort,
    q: str | None,
    dex: str | None,
    min_liquidity_usd: Decimal | None,
    min_volume_24h_usd: Decimal | None,
    min_change_h24: Decimal | None,
    max_change_h24: Decimal | None,
    min_pair_age_minutes: int | None,
    max_pair_age_minutes: int | None,
) -> bool:
    if data.get("kind") != "markets" or data.get("chain") != chain or data.get("sort") != sort:
        return False
    if data.get("q") != q:
        return False
    if data.get("dex") != dex:
        return False
    if data.get("min_liquidity_usd") != _decimal_text(min_liquidity_usd):
        return False
    if data.get("min_volume_24h_usd") != _decimal_text(min_volume_24h_usd):
        return False
    if data.get("min_change_h24") != _decimal_text(min_change_h24):
        return False
    if data.get("max_change_h24") != _decimal_text(max_change_h24):
        return False
    if data.get("min_pair_age_minutes") != min_pair_age_minutes:
        return False
    if data.get("max_pair_age_minutes") != max_pair_age_minutes:
        return False
    return True


@router.get("/api/v1/markets/stored", response_model=StoredMarketListOut)
def market_stored(
    chain: str = Query(default="all"),
    cursor: str | None = Query(default=None),
    page_cursor: str | None = Query(default=None, alias="page_cursor"),
    after: str | None = Query(default=None, alias="after"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    min_liquidity_usd: Decimal | None = Query(default=None, ge=0),
    min_volume_24h_usd: Decimal | None = Query(default=None, ge=0),
    min_change_h24: Decimal | None = Query(default=None),
    max_change_h24: Decimal | None = Query(default=None),
    min_pair_age_minutes: int | None = Query(default=None, ge=0),
    max_pair_age_minutes: int | None = Query(default=None, ge=0),
    dex: str | None = Query(default=None, max_length=80),
    session: Session = Depends(get_session),
) -> StoredMarketListOut:
    if chain != "all" and get_chain(chain) is None:
        raise HTTPException(
            status_code=404, detail={"code": "CHAIN_NOT_FOUND", "message": f"unknown chain {chain}"}
        )
    _validate_decimal_filter("min_liquidity_usd", min_liquidity_usd, non_negative=True)
    _validate_decimal_filter("min_volume_24h_usd", min_volume_24h_usd, non_negative=True)
    _validate_decimal_filter("min_change_h24", min_change_h24, non_negative=False)
    _validate_decimal_filter("max_change_h24", max_change_h24, non_negative=False)
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
    if dex is not None:
        dex = dex.strip() or None

    now = datetime.now(UTC)
    effective_cursor = cursor or page_cursor or after
    all_rows = stored_snapshots(session, chain=chain, limit=100000, offset=0)
    filtered, unknown = _apply_stored_filters(
        all_rows,
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

    if effective_cursor is not None:
        data = _decode_cursor(effective_cursor)
        if data.get("kind") != "stored" or data.get("chain") != chain:
            _invalid_cursor("cursor does not match this request")
        # filter match
        if data.get("dex") != dex:
            _invalid_cursor("cursor does not match this request")
        if data.get("min_liquidity_usd") != _decimal_text(min_liquidity_usd):
            _invalid_cursor("cursor does not match this request")
        if data.get("min_volume_24h_usd") != _decimal_text(min_volume_24h_usd):
            _invalid_cursor("cursor does not match this request")
        if data.get("min_change_h24") != _decimal_text(min_change_h24):
            _invalid_cursor("cursor does not match this request")
        if data.get("max_change_h24") != _decimal_text(max_change_h24):
            _invalid_cursor("cursor does not match this request")
        if data.get("min_pair_age_minutes") != min_pair_age_minutes:
            _invalid_cursor("cursor does not match this request")
        if data.get("max_pair_age_minutes") != max_pair_age_minutes:
            _invalid_cursor("cursor does not match this request")
        anchor_id = data.get("id")
        if not isinstance(anchor_id, str):
            _invalid_cursor("cursor is missing its anchor")
        anchor_index = next(
            (index for index, row in enumerate(filtered) if str(row.id) == anchor_id), None
        )
        if anchor_index is None:
            _invalid_cursor("cursor refers to a row that no longer exists")
        start = anchor_index + 1
        page_rows = filtered[start : start + limit]
    else:
        start = offset
        page_rows = filtered[start : start + limit]

    returned = len(page_rows)
    has_more = start + returned < total
    next_cursor = None
    if has_more and page_rows:
        next_cursor = _encode_cursor(
            {
                "v": 1,
                "kind": "stored",
                "chain": chain,
                "id": _stored_anchor(page_rows[-1]),
                "dex": dex,
                "min_liquidity_usd": _decimal_text(min_liquidity_usd),
                "min_volume_24h_usd": _decimal_text(min_volume_24h_usd),
                "min_change_h24": _decimal_text(min_change_h24),
                "max_change_h24": _decimal_text(max_change_h24),
                "min_pair_age_minutes": min_pair_age_minutes,
                "max_pair_age_minutes": max_pair_age_minutes,
            }
        )

    return StoredMarketListOut(
        rows=[_stored(row, now) for row in page_rows],
        as_of=now.isoformat(),
        offset=start,
        limit=limit,
        total=total,
        has_more=has_more,
        next_offset=start + returned if has_more else None,
        next_cursor=next_cursor,
        applied_filters=_stored_filters_payload(
            chain,
            None,
            dex,
            min_liquidity_usd,
            min_volume_24h_usd,
            min_change_h24,
            max_change_h24,
            min_pair_age_minutes,
            max_pair_age_minutes,
            unknown,
        ),
    )


@router.get("/api/v1/markets", response_model=MarketListOut)
async def markets(
    chain: str = Query(default="all"),
    q: str | None = Query(default=None, max_length=80),
    sort: MarketSort = Query(default="trending"),
    cursor: str | None = Query(default=None),
    page_cursor: str | None = Query(default=None, alias="page_cursor"),
    after: str | None = Query(default=None, alias="after"),
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
    if chain != "all" and get_chain(chain) is None:
        raise HTTPException(
            status_code=404, detail={"code": "CHAIN_NOT_FOUND", "message": f"unknown chain {chain}"}
        )
    _validate_decimal_filter("min_liquidity_usd", min_liquidity_usd, non_negative=True)
    _validate_decimal_filter("min_volume_24h_usd", min_volume_24h_usd, non_negative=True)
    _validate_decimal_filter("min_change_h24", min_change_h24, non_negative=False)
    _validate_decimal_filter("max_change_h24", max_change_h24, non_negative=False)
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

    effective_cursor = cursor or page_cursor or after
    if effective_cursor is not None:
        data = _decode_cursor(effective_cursor)
        if not _cursor_market_matches(
            data,
            chain=chain,
            sort=sort,
            q=q,
            dex=dex,
            min_liquidity_usd=min_liquidity_usd,
            min_volume_24h_usd=min_volume_24h_usd,
            min_change_h24=min_change_h24,
            max_change_h24=max_change_h24,
            min_pair_age_minutes=min_pair_age_minutes,
            max_pair_age_minutes=max_pair_age_minutes,
        ):
            _invalid_cursor("cursor does not match this request")
        anchor_id = data.get("id")
        if not isinstance(anchor_id, str):
            _invalid_cursor("cursor is missing its anchor")
        anchor_index = next(
            (index for index, row in enumerate(filtered) if _market_anchor(row) == anchor_id), None
        )
        if anchor_index is None:
            _invalid_cursor("cursor refers to a row that no longer exists")
        start = anchor_index + 1
        page_rows = filtered[start : start + limit]
    else:
        start = offset
        if start < 0:
            start = 0
        if start > total:
            start = total
        page_rows = filtered[start : start + limit]

    returned = len(page_rows)
    has_more = start + returned < total
    semantics = _sort_semantics(chain, q, coverage)
    semantics_value = (
        "per_chain_round_robin" if semantics == "per_chain_round_robin" else "global_sort"
    )

    next_cursor = None
    if has_more and page_rows:
        next_cursor = _encode_cursor(
            {
                "v": 1,
                "kind": "markets",
                "chain": chain,
                "sort": sort,
                "q": q,
                "dex": dex,
                "min_liquidity_usd": _decimal_text(min_liquidity_usd),
                "min_volume_24h_usd": _decimal_text(min_volume_24h_usd),
                "min_change_h24": _decimal_text(min_change_h24),
                "max_change_h24": _decimal_text(max_change_h24),
                "min_pair_age_minutes": min_pair_age_minutes,
                "max_pair_age_minutes": max_pair_age_minutes,
                "id": _market_anchor(page_rows[-1]),
            }
        )

    return MarketListOut(
        rows=[_live_row(row, now) for row in page_rows],
        source=SourceOut.model_validate(source.to_dict()),
        stale=source.is_stale(DEFAULT_STALENESS_SECONDS),
        coverage=None
        if coverage is None
        else [MarketCoverageOut.model_validate(item.to_dict()) for item in coverage],
        ordered_by="chain_round_robin" if semantics == "per_chain_round_robin" else "global_sort",
        sort=sort,
        sort_semantics=semantics_value,  # type: ignore[arg-type]
        offset=start,
        limit=limit,
        total=total,
        has_more=has_more,
        next_offset=start + returned if has_more else None,
        next_cursor=next_cursor,
        truncated=coverage is not None and sum(item.returned for item in coverage) > len(rows),
        applied_filters=_market_filters_payload(
            chain,
            q,
            dex,
            min_liquidity_usd,
            min_volume_24h_usd,
            min_change_h24,
            max_change_h24,
            min_pair_age_minutes,
            max_pair_age_minutes,
            unknown,
        ),
        population=_population(
            rows=rows,
            coverage=coverage,
            total=total,
            returned=returned,
            sort=sort,
            semantics=semantics_value,
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
    return _token_out(snapshot, datetime.now(UTC))


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


_QUOTE_QUERY_FIELDS = frozenset(
    {"chain", "token", "side", "amount_usd", "quantity", "slippage_bps"}
)


@router.get("/api/v1/quotes", response_model=QuoteOut)
async def quote(
    request: Request,
    chain: str,
    token: str,
    side: Literal["buy", "sell"],
    amount_usd: Decimal = Query(gt=0),
    quantity: Decimal | None = Query(default=None, gt=0),
    slippage_bps: int | None = Query(default=None, ge=0, le=10000),
    quotes: QuoteRouter = Depends(get_quote_router),
    settings: Settings = Depends(get_settings),
) -> QuoteOut:
    refuse_unknown_query(request, _QUOTE_QUERY_FIELDS)
    if get_chain(chain) is None:
        raise HTTPException(
            status_code=404, detail={"code": "CHAIN_NOT_FOUND", "message": f"unknown chain {chain}"}
        )
    if amount_usd > settings.max_quote_notional_usd:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "QUOTE_NOTIONAL_TOO_LARGE",
                "message": "quote notional exceeds the configured maximum",
                "retryable": False,
            },
        )
    if slippage_bps is not None and slippage_bps > settings.max_quote_slippage_bps:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "QUOTE_SLIPPAGE_TOO_HIGH",
                "message": "requested slippage exceeds the configured maximum",
                "retryable": False,
            },
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
    if result.price_impact_pct is not None:
        impact_bps = result.price_impact_pct * Decimal(10000)
        if impact_bps > settings.max_quote_slippage_bps:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "QUOTE_SLIPPAGE_TOO_HIGH",
                    "message": "quoted price impact exceeds the configured maximum",
                    "retryable": False,
                },
            )
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
