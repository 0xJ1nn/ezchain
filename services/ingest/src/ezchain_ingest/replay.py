"""Deterministic replay of a recorded market-event log.

The harness feeds the recorded file through the same contract and derives a state that
depends only on the events and the clock it is asked about. Two replays of the same
file at the same clock are equal; replaying later ages the observations without
writing anything back. A late event is counted, not reordered, and a duplicate
``(source, pair, source timestamp)`` is idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ezchain_core import DEFAULT_STALENESS_SECONDS, MarketEvent

from .eventlog import read_events


@dataclass(frozen=True)
class PairObservation:
    """The latest observation for one pair, aged at read time."""

    event: MarketEvent
    age_seconds: float
    stale: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event.to_dict(),
            "age_seconds": self.age_seconds,
            "stale": self.stale,
        }


@dataclass(frozen=True)
class ReplayState:
    """Derived state of one replay; equality is the replay-determinism check."""

    observed: int
    duplicates: int
    late: int
    pairs: tuple[PairObservation, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "observed": self.observed,
            "duplicates": self.duplicates,
            "late": self.late,
            "pairs": [pair.to_dict() for pair in self.pairs],
        }


def derive_state(
    events: tuple[MarketEvent, ...],
    *,
    now: datetime | None = None,
    max_age_seconds: float = DEFAULT_STALENESS_SECONDS,
) -> ReplayState:
    """Derive one deterministic state from events and a read clock."""
    seen: set[tuple[str, str, datetime]] = set()
    duplicates = 0
    late = 0
    latest: dict[tuple[str, str, str], MarketEvent] = {}
    latest_key: dict[tuple[str, str, str], tuple[datetime, datetime]] = {}

    for event in events:
        identity = (event.source.name, event.pair, event.source.observed_at)
        if identity in seen:
            duplicates += 1
            continue
        seen.add(identity)

        key = (event.chain, event.venue, event.pair)
        clock = (event.source.observed_at, event.observed_at)
        previous = latest_key.get(key)
        if previous is None:
            latest[key] = event
            latest_key[key] = clock
            continue
        if clock < previous:
            late += 1
            continue
        if clock > previous:
            latest[key] = event
            latest_key[key] = clock

    pairs = tuple(
        PairObservation(
            event=latest[key],
            age_seconds=latest[key].age_seconds(now),
            stale=latest[key].is_stale(max_age_seconds, now),
        )
        for key in sorted(latest)
    )
    return ReplayState(
        observed=len(events),
        duplicates=duplicates,
        late=late,
        pairs=pairs,
    )


class ReplayHarness:
    """Feed one recorded file and derive the same state on every run."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_age_seconds: float = DEFAULT_STALENESS_SECONDS,
    ) -> None:
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        self._path = Path(path)
        self._max_age_seconds = float(max_age_seconds)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> tuple[MarketEvent, ...]:
        return read_events(self._path)

    def derive(self, now: datetime | None = None) -> ReplayState:
        return derive_state(
            self.load(),
            now=now,
            max_age_seconds=self._max_age_seconds,
        )


__all__ = ["PairObservation", "ReplayHarness", "ReplayState", "derive_state"]
