"""The discovery router: the new-pairs feed with its coverage and safety evidence.

One request ranks one bounded market read. The read model is
:mod:`ezchain_api.services.discovery`; this module owns the HTTP shape and the honesty
rules the response makes:

* every displayed number carries the source it came from and the age of that observation;
  an unknown value is ``null`` with ``known: false``, never a zero;
* every dropped row is counted under its own reason, so a reader can see why the feed is
  short instead of guessing, and the adapter's per-chain coverage is carried through so "the
  source failed" is never flattened into "there was nothing new";
* ``truncated`` says the page was cut by ``limit`` rather than the population ending;
* a row is ``tradeable`` only with positive safety evidence, and this build has none, so
  every row is listed with ``safety.status == "unknown"`` and a reason.

An empty answer carries a reason code and never a filler row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query

from ezchain_adapters import AdapterError, DexScreenerClient, get_chain
from ezchain_core import MarketRow

from ..config import Settings, get_settings
from ..deps import get_dexscreener
from ..errors import adapter_http_error
from ..schemas import (
    DiscoveryAppliedOut,
    DiscoveryCoverageOut,
    DiscoveryMetricOut,
    DiscoveryNewPairOut,
    DiscoverySafetyOut,
    MarketCoverageOut,
    NewPairsOut,
    SourceOut,
)
from ..services.discovery import (
    DISCOVERY_CANDIDATE_ROWS,
    DISCOVERY_DEFAULT_ROWS,
    DISCOVERY_MAX_PAIR_AGE_SECONDS,
    DISCOVERY_MAX_ROWS,
    REASON_NO_MARKET_ROWS,
    REASON_NO_QUALIFIED_MOVERS,
    MetricUnit,
    NewPair,
    rank_new_pairs,
)
from ..services.market_reads import provenance

router = APIRouter(tags=["discovery"])


def _decimal_text(value: Decimal | None) -> str | None:
    """Plain decimal text, never exponent notation. Unknown stays null."""
    if value is None:
        return None
    if value == 0:
        return "0"
    if not value.is_finite():
        return None
    return format(value, "f")


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _metric(
    value: Decimal | None, row: MarketRow, *, age_seconds: int, unit: MetricUnit
) -> DiscoveryMetricOut:
    """One displayed number with the source it came from and how old it is.

    A missing value keeps ``value`` null and reports ``known: false``, but still names the
    source: "the provider was asked and did not answer" is different from "nobody asked".
    The source and age are the row's, because every number on a pair row comes from the
    same provider answer.
    """
    text = _decimal_text(value)
    return DiscoveryMetricOut(
        value=text,
        known=text is not None,
        unit=unit,
        source=SourceOut.model_validate(row.source.to_dict()),
        age_seconds=age_seconds,
    )


def _row_out(pair: NewPair, now: datetime, *, max_mark_age_seconds: int) -> DiscoveryNewPairOut:
    row = pair.row
    freshness = provenance(row.source, max_age_seconds=max_mark_age_seconds, now=now)
    age_seconds = pair.mark_age_seconds
    return DiscoveryNewPairOut(
        chain=row.chain,
        token_address=row.base_token,
        pair_address=row.pair_address,
        dex=row.dex,
        symbol=row.symbol,
        name=row.name,
        url=row.url,
        pair_created_at=(
            None if row.pair_created_at is None else _utc(row.pair_created_at).isoformat()
        ),
        pair_age_seconds=pair.pair_age_seconds,
        price_usd=_metric(row.price_usd, row, age_seconds=age_seconds, unit="usd"),
        liquidity_usd=_metric(row.liquidity_usd, row, age_seconds=age_seconds, unit="usd"),
        volume_24h_usd=_metric(row.volume_24h_usd, row, age_seconds=age_seconds, unit="usd"),
        price_change_h24_pct=_metric(
            row.price_change_h24, row, age_seconds=age_seconds, unit="percent"
        ),
        source=SourceOut.model_validate(row.source.to_dict()),
        observed_at=_utc(row.source.observed_at).isoformat(),
        age_seconds=age_seconds,
        stale=True if freshness.stale is None else freshness.stale,
        safety=DiscoverySafetyOut(
            status=pair.safety.status,
            reason=pair.safety.reason,
            checked_at=(
                None if pair.safety.checked_at is None else _utc(pair.safety.checked_at).isoformat()
            ),
        ),
        tradeable=pair.tradeable,
        not_tradeable_reason=pair.not_tradeable_reason,
    )


@router.get("/api/v1/discovery/new-pairs", response_model=NewPairsOut)
async def new_pairs(
    chain: str = Query(default="all"),
    limit: int = Query(default=DISCOVERY_DEFAULT_ROWS, ge=1, le=DISCOVERY_MAX_ROWS),
    adapter: DexScreenerClient = Depends(get_dexscreener),
    settings: Settings = Depends(get_settings),
) -> NewPairsOut:
    """The new-pairs feed for one chain, or every registered chain.

    The answer is bounded: at most ``DISCOVERY_CANDIDATE_ROWS`` discovery rows are ranked
    and at most ``DISCOVERY_MAX_ROWS`` rows are returned. The response carries the coverage
    of the pass — candidates, deduped, per-reason drops and whether the page was cut — plus
    the source and the read clock.
    """
    if chain != "all" and get_chain(chain) is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CHAIN_NOT_FOUND", "message": f"unknown chain {chain}"},
        )

    try:
        rows, source, coverage = await adapter.discover_markets(
            chain=chain, sort="new", limit=DISCOVERY_CANDIDATE_ROWS
        )
    except AdapterError as exc:
        raise adapter_http_error(exc) from exc

    now = datetime.now(UTC)
    min_pair_age_seconds = settings.min_pair_age_minutes * 60
    # No safety provider exists in this build (ADR-0046), so the read model's default check
    # is the adapter's own SAFETY_UNKNOWN and no row can be marked tradeable.
    ranking = rank_new_pairs(
        rows,
        now=now,
        min_pair_age_seconds=min_pair_age_seconds,
        max_pair_age_seconds=DISCOVERY_MAX_PAIR_AGE_SECONDS,
        min_liquidity_usd=settings.min_liquidity_usd,
        max_mark_age_seconds=settings.max_mark_age_seconds,
    )
    selected = ranking.rows[:limit]

    if not rows:
        reason: str | None = REASON_NO_MARKET_ROWS
    elif not ranking.rows:
        reason = REASON_NO_QUALIFIED_MOVERS
    else:
        reason = None

    return NewPairsOut(
        chain=chain,
        as_of=now.isoformat(),
        rows=[
            _row_out(pair, now, max_mark_age_seconds=settings.max_mark_age_seconds)
            for pair in selected
        ],
        total=len(ranking.rows),
        limit=limit,
        truncated=len(ranking.rows) > len(selected),
        reason=reason,
        tradeable_on_page=sum(1 for pair in selected if pair.tradeable),
        coverage=DiscoveryCoverageOut(
            candidates=ranking.candidates,
            deduped=ranking.deduped,
            dropped=dict(ranking.exclusions),
            truncated=len(ranking.rows) > len(selected),
            # Carried through rather than defaulted: a chain with ``error`` or ``no_seeds``
            # means the source failed or was never asked, which is not "no new pairs".
            chains=(
                None
                if coverage is None
                else [MarketCoverageOut.model_validate(item.to_dict()) for item in coverage]
            ),
        ),
        applied=DiscoveryAppliedOut(
            chain=chain,
            min_pair_age_seconds=min_pair_age_seconds,
            max_pair_age_seconds=DISCOVERY_MAX_PAIR_AGE_SECONDS,
            min_liquidity_usd=_decimal_text(settings.min_liquidity_usd) or "0",
            max_mark_age_seconds=settings.max_mark_age_seconds,
            candidate_rows=DISCOVERY_CANDIDATE_ROWS,
            candidate_sort="new",
            limit=limit,
        ),
        source=SourceOut.model_validate(source.to_dict()),
        stale=source.is_stale(settings.max_mark_age_seconds, now),
        safety_policy="fail_closed",
    )


__all__ = ["router"]
