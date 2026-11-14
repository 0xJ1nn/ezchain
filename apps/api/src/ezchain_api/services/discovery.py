"""The new-pairs read model: one bounded, fail-closed ranking pass.

The Launches page used to read the generic markets feed and call the newest rows
"launches". That feed could not say which rows had been dropped, could not prove a row was
young enough to be interesting, and had nowhere to put safety evidence. This module is the
read model that replaces it, and it is deliberately narrow:

* the candidates are one bounded discovery read;
* the read is deduplicated to exactly one row per ``(chain, base_token)``, keeping the
  deepest pool, because a token's market is its deepest pool and a second pool would
  otherwise duplicate the row;
* a row is dropped, and counted under its own reason, when its mark is stale or dated
  ahead of the clock, when its pair age is unknown, younger than the listing floor or
  older than the feed window, or when its liquidity is unknown or under its floor;
* the survivors are ranked newest pair first, deepest pool second.

Safety is fail closed. A row is tradeable only when a safety check returns positive
evidence (``status == "verified"``). There is no safety source in this build — the
DexScreener adapter ships ``SAFETY_UNKNOWN`` — so with the real adapter every row is
listed with ``safety.status == "unknown"`` and **none** is tradeable. Missing evidence is
unknown, never a pass, and an absent check is not an empty success.

The floors are *passed in*, not defaulted here: the age and liquidity floors are the
existing ``Settings.min_pair_age_minutes`` and ``Settings.min_liquidity_usd`` and the mark
budget is ``Settings.max_mark_age_seconds`` (CONTRACT I7 — one home per number). Only the
feed window, which Settings has no field for, is a module constant, and a discovery
section in ``Settings`` is the documented follow-up. The response echoes every bound under
``applied`` so a reader never has to guess which floors produced the page.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from ezchain_adapters.dexscreener import SAFETY_UNKNOWN
from ezchain_core import MarketRow

from .market_reads import provenance

#: How many discovery rows one pass may rank.
DISCOVERY_CANDIDATE_ROWS = 200
#: The largest number of rows one response may carry.
DISCOVERY_MAX_ROWS = 50
#: The number of rows one response carries when the caller asks for none.
DISCOVERY_DEFAULT_ROWS = 20
#: A pair older than this is not a *new* pair: the feed window. No Settings field owns this
#: number yet; a discovery section in ``Settings`` is the follow-up.
DISCOVERY_MAX_PAIR_AGE_SECONDS = 7 * 24 * 60 * 60

#: The reason codes an empty answer may carry. Both already exist in the taxonomy and are
#: reused rather than minted for this read model.
REASON_NO_MARKET_ROWS = "NO_MARKET_ROWS"
REASON_NO_QUALIFIED_MOVERS = "NO_QUALIFIED_MOVERS"

#: Why rows did not reach the table. These are coverage counters, not reason codes: each
#: dropped row is counted under the first reason that applies to it, and unknown keeps its
#: own bucket so it is never counted as a zero that failed a filter.
DROP_REASONS = (
    "unknown_token",
    "duplicate_token",
    "pair_age_unknown",
    "pair_too_young",
    "pair_too_old",
    "mark_future",
    "mark_stale",
    "unknown_liquidity",
    "below_liquidity_floor",
)


#: The unit of one displayed number. Declared here so the read model and the schema cannot
#: disagree about what a value means.
MetricUnit = Literal["usd", "percent", "seconds"]
#: The three states of safety evidence. ``verified`` is the only one that may trade.
SafetyStatus = Literal["verified", "flagged", "unknown"]


@dataclass(frozen=True)
class SafetyEvidence:
    """What a safety check could prove about one token.

    ``reason`` is mandatory: a token that is not tradeable always says why. Positive
    evidence is exactly ``status == "verified"``; every other status leaves the row
    non-tradeable.
    """

    status: SafetyStatus
    reason: str
    checked_at: datetime | None = None

    @property
    def verified(self) -> bool:
        return self.status == "verified"


def _status(value: object) -> SafetyStatus:
    """A declared status, with anything unrecognised collapsed to ``unknown``.

    A source that invents a status this build does not understand has proven nothing, so it
    fails closed into ``unknown`` rather than being trusted by accident.
    """
    if value == "verified":
        return "verified"
    if value == "flagged":
        return "flagged"
    return "unknown"


#: The evidence this build can produce: the adapter implements no safety screening, so the
#: answer is ``unknown``. It is read from the adapter's own constant so the read model and
#: the token page cannot disagree about what unknown means.
UNKNOWN_SAFETY = SafetyEvidence(
    status=_status(SAFETY_UNKNOWN["status"]),
    reason=str(SAFETY_UNKNOWN["reason"]),
)

SafetyCheck = Callable[[MarketRow], SafetyEvidence]


def unknown_safety(_row: MarketRow) -> SafetyEvidence:
    """The only safety check that exists today, and it proves nothing."""
    return UNKNOWN_SAFETY


@dataclass(frozen=True)
class NewPair:
    """One surviving row with the ages it was judged on and its safety evidence."""

    row: MarketRow
    pair_age_seconds: int
    mark_age_seconds: int
    safety: SafetyEvidence

    @property
    def tradeable(self) -> bool:
        return self.safety.verified

    @property
    def not_tradeable_reason(self) -> str | None:
        return None if self.safety.verified else self.safety.reason


@dataclass
class Ranking:
    """The ranked survivors plus the count of every row that did not reach the table."""

    rows: list[NewPair] = field(default_factory=list)
    exclusions: dict[str, int] = field(default_factory=dict)
    candidates: int = 0
    deduped: int = 0

    def excluded(self, reason: str) -> None:
        self.exclusions[reason] = self.exclusions.get(reason, 0) + 1


def _aware(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _preference(row: MarketRow, observed: datetime) -> tuple[Any, ...]:
    """How two pools of one token compare.

    The deepest known pool wins, because that pool is the token's market. A known volume
    beats an unknown one and the freshest observation breaks the remaining tie. Unknown is
    never treated as zero: an unknown value only loses to a known one.
    """
    return (
        row.liquidity_usd is not None,
        row.liquidity_usd if row.liquidity_usd is not None else Decimal(0),
        row.volume_24h_usd is not None,
        row.volume_24h_usd if row.volume_24h_usd is not None else Decimal(0),
        observed,
    )


def rank_new_pairs(
    rows: list[MarketRow],
    *,
    now: datetime,
    min_pair_age_seconds: int,
    max_pair_age_seconds: int,
    min_liquidity_usd: Decimal,
    max_mark_age_seconds: int,
    safety_check: SafetyCheck = unknown_safety,
) -> Ranking:
    """Rank a bounded market read into the new-pairs feed.

    The function is pure: the same rows, clock and bounds always produce the same ranking,
    and a row that cannot be judged is counted rather than guessed. The bounds are
    arguments, not defaults, so there is exactly one home for each number.
    """
    ranking = Ranking(candidates=len(rows))

    best: dict[str, tuple[tuple[Any, ...], MarketRow]] = {}
    for row in rows:
        if not row.base_token.strip():
            ranking.excluded("unknown_token")
            continue
        observed = _aware(row.source.observed_at)
        key = _preference(row, observed)
        token = f"{row.chain.strip().lower()}:{row.base_token.strip().lower()}"
        current = best.get(token)
        if current is None:
            best[token] = (key, row)
            continue
        ranking.excluded("duplicate_token")
        if key > current[0] or (key == current[0] and row.pair_address < current[1].pair_address):
            best[token] = (key, row)
    ranking.deduped = len(best)

    survivors: list[NewPair] = []
    for _token, (_key, row) in best.items():
        mark = provenance(row.source, max_age_seconds=max_mark_age_seconds, now=now)
        if mark.age_seconds is None:
            # A mark dated past the tolerated skew describes no usable observation.
            ranking.excluded("mark_future")
            continue
        if mark.stale:
            ranking.excluded("mark_stale")
            continue
        if row.pair_created_at is None:
            ranking.excluded("pair_age_unknown")
            continue
        pair_age_seconds = int(max(0.0, (now - _aware(row.pair_created_at)).total_seconds()))
        if pair_age_seconds < min_pair_age_seconds:
            ranking.excluded("pair_too_young")
            continue
        if pair_age_seconds > max_pair_age_seconds:
            ranking.excluded("pair_too_old")
            continue
        if row.liquidity_usd is None:
            ranking.excluded("unknown_liquidity")
            continue
        if row.liquidity_usd < min_liquidity_usd:
            ranking.excluded("below_liquidity_floor")
            continue
        survivors.append(
            NewPair(
                row=row,
                pair_age_seconds=pair_age_seconds,
                mark_age_seconds=mark.age_seconds,
                safety=safety_check(row),
            )
        )

    # Newest pair first, deepest pool second, and the pair address makes the order total so
    # the same read always renders the same table.
    survivors.sort(
        key=lambda pair: (
            pair.pair_age_seconds,
            -(pair.row.liquidity_usd or Decimal(0)),
            pair.row.pair_address,
        )
    )
    ranking.rows = survivors
    return ranking


__all__ = [
    "DISCOVERY_CANDIDATE_ROWS",
    "DISCOVERY_DEFAULT_ROWS",
    "DISCOVERY_MAX_PAIR_AGE_SECONDS",
    "DISCOVERY_MAX_ROWS",
    "DROP_REASONS",
    "REASON_NO_MARKET_ROWS",
    "REASON_NO_QUALIFIED_MOVERS",
    "UNKNOWN_SAFETY",
    "NewPair",
    "Ranking",
    "SafetyCheck",
    "SafetyEvidence",
    "SafetyStatus",
    "MetricUnit",
    "rank_new_pairs",
    "unknown_safety",
]
