"""Server-ranked market movers.

The Signals page used to sort the discovery feed in the browser: it fetched an
unfiltered market list and picked the biggest movers itself. The browser could not tell
a thin pool from a deep one, could not tell a fresh mark from a stale one, and had no
way to say that a row had been dropped. This module is the read model that replaces it.

One request ranks one window and one direction over a bounded market read:

* the read is deduplicated to one row per token, keeping the deepest known pool;
* a row with an unknown change, an unknown liquidity or a mark older than the freshness
  budget is excluded and counted under its own reason, never shown;
* the survivors are ranked by the change and cut at the requested row count.

When nothing qualifies the answer is an empty list with a reason code. A mover is never
invented to fill the table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query

from ezchain_adapters import AdapterError, DexScreenerClient, SortMode, get_chain
from ezchain_core import MarketRow

from ..config import Settings, get_settings
from ..deps import get_dexscreener
from ..errors import adapter_http_error
from ..schemas import (
    SignalAppliedOut,
    SignalDirection,
    SignalExclusionsOut,
    SignalMoverOut,
    SignalMoversOut,
    SignalWindow,
    SourceOut,
)

router = APIRouter(tags=["signals"])

#: How many discovery rows the market read may look at before ranking.
SIGNALS_CANDIDATE_ROWS = 200
#: The largest number of movers one response may carry.
SIGNALS_MAX_ROWS = 50
#: The number of movers one response carries when the caller asks for none.
SIGNALS_DEFAULT_ROWS = 10
#: The longest a mark may be old before it is excluded, in seconds.
SIGNALS_MAX_AGE_SECONDS = 3600

# The floor is echoed back to the caller. Without a bound, Decimal accepts 1e1000000 and
# formatting it would expand to a megabyte of digits. The supported range covers every
# realistic USD figure.
FLOOR_MAX_ABS = Decimal("1e18")

#: The reason codes an empty answer may carry.
REASON_NO_MARKET_ROWS = "NO_MARKET_ROWS"
REASON_NO_QUALIFIED_MOVERS = "NO_QUALIFIED_MOVERS"

EXCLUSION_REASONS = (
    "unknown_token",
    "duplicate_token",
    "stale",
    "unknown_change",
    "unknown_liquidity",
    "below_liquidity_floor",
    "wrong_direction",
)


def _invalid(message: str) -> NoReturn:
    raise HTTPException(
        status_code=422,
        detail={"code": "INVALID_FILTER", "message": message, "retryable": False},
    )


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _decimal_text(value: Decimal) -> str:
    """Plain decimal text, never exponent notation."""
    if value == 0:
        return "0"
    if not value.is_finite():
        return str(value)
    return format(value, "f")


def _validate_floor(value: Decimal | None) -> None:
    if value is None:
        return
    if not value.is_finite():
        _invalid("min_liquidity_usd must be finite")
    if value.as_tuple().sign:
        _invalid("min_liquidity_usd must be greater than or equal to zero")
    if value > FLOOR_MAX_ABS:
        _invalid(f"min_liquidity_usd must not exceed {FLOOR_MAX_ABS:f}")


@dataclass(frozen=True)
class Mover:
    """One surviving row with the change it was ranked on."""

    row: MarketRow
    change: Decimal
    observed_at: datetime
    age_seconds: int


@dataclass
class Ranking:
    """The ranked survivors plus the count of every row that did not reach the table."""

    rows: list[Mover] = field(default_factory=list)
    exclusions: dict[str, int] = field(default_factory=dict)
    rows_read: int = 0

    def excluded(self, reason: str) -> None:
        self.exclusions[reason] = self.exclusions.get(reason, 0) + 1


def _change_of(row: MarketRow, window: SignalWindow) -> Decimal | None:
    return row.price_change_h1 if window == "h1" else row.price_change_h24


def _preference(row: MarketRow, observed: datetime) -> tuple[Any, ...]:
    """How two pairs of one token compare.

    The deepest known pool wins, because that pool is the token's market and the one the
    order path would price against. A known volume beats an unknown one, and the freshest
    observation breaks the remaining tie. Unknown is never treated as zero: an unknown
    value only loses to a known one.
    """
    return (
        row.liquidity_usd is not None,
        row.liquidity_usd if row.liquidity_usd is not None else Decimal(0),
        row.volume_24h_usd is not None,
        row.volume_24h_usd if row.volume_24h_usd is not None else Decimal(0),
        observed,
    )


def rank_movers(
    rows: list[MarketRow],
    *,
    window: SignalWindow,
    direction: SignalDirection,
    min_liquidity_usd: Decimal,
    max_age_seconds: int,
    now: datetime,
) -> Ranking:
    """Rank one window and one direction over a market read.

    The function is pure: the same rows and the same bounds always produce the same
    ranking, and a row that cannot be judged is counted rather than guessed.
    """
    ranking = Ranking(rows_read=len(rows))

    best: dict[str, tuple[tuple[Any, ...], MarketRow]] = {}
    for row in rows:
        if not row.base_token:
            ranking.excluded("unknown_token")
            continue
        observed = _aware(row.source.observed_at)
        key = _preference(row, observed)
        token = f"{row.chain.strip().lower()}:{row.base_token.strip().lower()}"
        current = best.get(token)
        if current is None:
            best[token] = (key, row)
            continue
        if key > current[0] or (key == current[0] and row.pair_address < current[1].pair_address):
            best[token] = (key, row)
    ranking.exclusions["duplicate_token"] = (
        ranking.rows_read - ranking.exclusions.get("unknown_token", 0) - len(best)
    )

    survivors: list[Mover] = []
    for _token, (_key, row) in best.items():
        observed = _aware(row.source.observed_at)
        age_seconds = int(max(0.0, (now - observed).total_seconds()))
        if age_seconds > max_age_seconds:
            ranking.excluded("stale")
            continue
        change = _change_of(row, window)
        if change is None:
            ranking.excluded("unknown_change")
            continue
        if row.liquidity_usd is None:
            ranking.excluded("unknown_liquidity")
            continue
        if row.liquidity_usd < min_liquidity_usd:
            ranking.excluded("below_liquidity_floor")
            continue
        if (direction == "up" and change <= 0) or (direction == "down" and change >= 0):
            ranking.excluded("wrong_direction")
            continue
        survivors.append(
            Mover(row=row, change=change, observed_at=observed, age_seconds=age_seconds)
        )

    # The strongest move first; the deepest pool breaks a tie; the pair address makes
    # the order total, so the same read always renders the same table.
    survivors.sort(
        key=lambda mover: (
            -mover.change if direction == "up" else mover.change,
            -(mover.row.liquidity_usd or Decimal(0)),
            mover.row.pair_address,
        )
    )
    ranking.rows = survivors
    return ranking


def _candidate_sort(window: SignalWindow) -> SortMode:
    """The provider ranking the candidate set is drawn from.

    The 24-hour window ranks the provider's own 24-hour change set. The 1-hour window
    ranks the trending set, because the provider exposes no 1-hour ranking.
    """
    return "change_h24" if window == "h24" else "trending"


@router.get("/api/v1/signals/movers", response_model=SignalMoversOut)
async def signal_movers(
    window: SignalWindow = Query(default="h1"),
    direction: SignalDirection = Query(default="up"),
    chain: str = Query(default="all"),
    min_liquidity_usd: Decimal | None = Query(default=None, ge=0),
    max_age_seconds: int | None = Query(default=None, ge=1, le=SIGNALS_MAX_AGE_SECONDS),
    limit: int = Query(default=SIGNALS_DEFAULT_ROWS, ge=1, le=SIGNALS_MAX_ROWS),
    adapter: DexScreenerClient = Depends(get_dexscreener),
    settings: Settings = Depends(get_settings),
) -> SignalMoversOut:
    """The ranked movers for one window and one direction.

    The answer is bounded: at most ``SIGNALS_CANDIDATE_ROWS`` discovery rows are ranked
    and at most ``SIGNALS_MAX_ROWS`` movers are returned. Every row carries its source,
    its observation time and its age. A read that produces no mover returns an empty
    list with a reason code instead of a fabricated row.
    """
    if chain != "all" and get_chain(chain) is None:
        raise HTTPException(
            status_code=404, detail={"code": "CHAIN_NOT_FOUND", "message": f"unknown chain {chain}"}
        )
    _validate_floor(min_liquidity_usd)

    floor = settings.min_liquidity_usd if min_liquidity_usd is None else min_liquidity_usd
    budget = min(
        settings.max_quote_age_seconds if max_age_seconds is None else max_age_seconds,
        SIGNALS_MAX_AGE_SECONDS,
    )
    sort = _candidate_sort(window)

    try:
        rows, source, _coverage = await adapter.discover_markets(
            chain=chain, sort=sort, limit=SIGNALS_CANDIDATE_ROWS
        )
    except AdapterError as exc:
        raise adapter_http_error(exc) from exc

    now = datetime.now(UTC)
    ranking = rank_movers(
        rows,
        window=window,
        direction=direction,
        min_liquidity_usd=floor,
        max_age_seconds=budget,
        now=now,
    )

    if not rows:
        reason: str | None = REASON_NO_MARKET_ROWS
    elif not ranking.rows:
        reason = REASON_NO_QUALIFIED_MOVERS
    else:
        reason = None

    selected = ranking.rows[:limit]
    movers = [
        SignalMoverOut(
            rank=index,
            chain=mover.row.chain,
            token_address=mover.row.base_token,
            pair_address=mover.row.pair_address,
            dex=mover.row.dex,
            symbol=mover.row.symbol,
            name=mover.row.name,
            price_usd=str(mover.row.price_usd),
            liquidity_usd=str(mover.row.liquidity_usd),
            volume_24h_usd=(
                None if mover.row.volume_24h_usd is None else str(mover.row.volume_24h_usd)
            ),
            change_pct=str(mover.change),
            source=SourceOut.model_validate(mover.row.source.to_dict()),
            observed_at=mover.observed_at.isoformat(),
            age_seconds=mover.age_seconds,
        )
        for index, mover in enumerate(selected, start=1)
    ]

    return SignalMoversOut(
        window=window,
        direction=direction,
        as_of=now.isoformat(),
        rows=movers,
        total=len(ranking.rows),
        limit=limit,
        truncated=len(ranking.rows) > len(movers),
        reason=reason,
        rows_read=ranking.rows_read,
        exclusions=SignalExclusionsOut(**ranking.exclusions),
        applied=SignalAppliedOut(
            chain=chain,
            window=window,
            direction=direction,
            min_liquidity_usd=_decimal_text(floor),
            max_age_seconds=budget,
            candidate_rows=SIGNALS_CANDIDATE_ROWS,
            candidate_sort=sort,
            limit=limit,
        ),
        source=SourceOut.model_validate(source.to_dict()),
        stale=source.is_stale(budget, now),
    )


__all__ = [
    "EXCLUSION_REASONS",
    "REASON_NO_MARKET_ROWS",
    "REASON_NO_QUALIFIED_MOVERS",
    "SIGNALS_CANDIDATE_ROWS",
    "SIGNALS_DEFAULT_ROWS",
    "SIGNALS_MAX_AGE_SECONDS",
    "SIGNALS_MAX_ROWS",
    "Mover",
    "Ranking",
    "rank_movers",
    "router",
]
