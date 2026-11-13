"""Read-model provenance for market reads, taken from the one market contract.

Every market read model reports where a number came from and how old it is. The rule is
the contract's: ``MarketEvent.age_seconds`` is ``SourceRef.age_seconds``, and
``MarketEvent.is_stale`` is ``SourceRef.is_stale``, both measured against the reader's
clock. A read model holds the source and the observation time, not a full observation, so
it applies the same rule directly instead of storing a freshness flag or trusting the
adapter that happened to answer.

Unknown stays unknown: a row whose source is missing, blank or the ``unknown`` sentinel has
no observation behind it, so the read model reports no source and no age. It never invents
an age of zero, and it never presents an unprovenanced row as fresh.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ezchain_core import DEFAULT_STALENESS_SECONDS, SourceRef, utcnow

UNKNOWN_SOURCE = "unknown"


@dataclass(frozen=True)
class Provenance:
    """Where one displayed market number came from, and how old it is.

    ``age_seconds`` and ``stale`` are null together when there is no contract source to
    age. ``stale`` is ``None`` then, never ``False``: a caller must decide how to display
    unknown rather than being handed a fresh-looking default.
    """

    source: SourceRef | None
    age_seconds: int | None
    stale: bool | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": None if self.source is None else self.source.to_dict(),
            "age_seconds": self.age_seconds,
            "stale": self.stale,
        }


def source_ref(
    name: str | None, observed_at: datetime | None, url: str | None = None
) -> SourceRef | None:
    """The contract source for a stored name and clock, or ``None`` when it is unknown.

    The persisted tables keep a source name and an observation time. A blank name, the
    ``unknown`` sentinel a writer uses when the adapter reported nothing, or a missing
    clock describes no observation, so it is not dressed up as one.
    """
    if name is None or observed_at is None:
        return None
    trimmed = name.strip()
    if not trimmed or trimmed.casefold() == UNKNOWN_SOURCE:
        return None
    return SourceRef(trimmed, url, observed_at)


def provenance(
    source: SourceRef | None,
    *,
    max_age_seconds: float = DEFAULT_STALENESS_SECONDS,
    now: datetime | None = None,
) -> Provenance:
    """Age and staleness of one observation at read time, exactly as the contract derives them."""
    if source is None:
        return Provenance(None, None, None)
    clock = now or utcnow()
    return Provenance(
        source=source,
        age_seconds=int(source.age_seconds(clock)),
        stale=source.is_stale(max_age_seconds, clock),
    )


__all__ = ["Provenance", "UNKNOWN_SOURCE", "provenance", "source_ref"]
