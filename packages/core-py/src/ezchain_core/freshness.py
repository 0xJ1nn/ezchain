"""Source freshness: how old a price may be before it stops counting.

Unknown is never zero. A read past its source class's budget returns nothing and names the
stable reason code that refused it; it never hands the last value back as if it were
current. An observation dated ahead of the clock is a clock fault, so it is ``future`` and
not fresh. A source with no observation at all is ``unknown``, which is also not fresh.

The same module raises ``provider_stale`` once per fresh→stale transition, so an operator
sees one alert per outage rather than one per request.

Budgets are per source class (``mark``, ``quote``, ``candle``, ``feed``) and configurable:
a caller builds one :class:`FreshnessPolicy` from its settings and passes the same instance
to every reader, so the mark gate, the quote gate and the alert all agree.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

from .types import MARK_FUTURE_SKEW_SECONDS, MarkStatus, SourceRef, utcnow

DEFAULT_MAX_AGE_SECONDS: Final = 60.0

# Per-class defaults, in seconds. A class is a kind of read, not a venue: two mark sources
# can have different budgets without the code naming either of them.
DEFAULT_MAX_AGE_BY_CLASS: Final[dict[str, float]] = {
    "mark": 60.0,
    "quote": 60.0,
    "candle": 300.0,
    "feed": 60.0,
}

_QUOTE_REASON: Final = "QUOTE_STALE"
_SNAPSHOT_REASON: Final = "SNAPSHOT_STALE"
_PROVIDER_STALE: Final = "provider_stale"


def stale_reason(source_class: str) -> str:
    """The stable reason code a stale read of ``source_class`` reports."""
    return _QUOTE_REASON if source_class == "quote" else _SNAPSHOT_REASON


def _iso(observed: datetime) -> str:
    return (observed.astimezone(UTC) if observed.tzinfo is not None else observed).isoformat()


@dataclass(frozen=True)
class FreshnessVerdict:
    """The server's own freshness verdict for one observation."""

    status: MarkStatus
    age_seconds: float | None
    reason: str | None
    source_class: str = "mark"

    @property
    def usable(self) -> bool:
        return self.status == "fresh"


@dataclass(frozen=True)
class FreshnessPolicy:
    """Per-source-class age budgets and the read gate that applies them."""

    max_age_by_class: Mapping[str, float] = field(
        default_factory=lambda: dict(DEFAULT_MAX_AGE_BY_CLASS)
    )
    default_max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS
    # The one tolerated skew shared with the risk gate: beyond it an observation is a
    # clock fault, not a fresh read.
    max_future_skew_seconds: float = MARK_FUTURE_SKEW_SECONDS

    def __post_init__(self) -> None:
        if self.default_max_age_seconds <= 0:
            raise ValueError("default_max_age_seconds must be positive")
        for source_class, budget in self.max_age_by_class.items():
            if budget <= 0:
                raise ValueError(f"max age for {source_class!r} must be positive")

    def budget(self, source_class: str = "mark") -> float:
        return self.max_age_by_class.get(source_class, self.default_max_age_seconds)

    def classify(
        self,
        source: SourceRef | None,
        *,
        source_class: str = "mark",
        now: datetime | None = None,
    ) -> FreshnessVerdict:
        """Classify one observation. Missing, stale and future-dated are never fresh."""
        if source is None:
            return FreshnessVerdict("unknown", None, None, source_class)
        # A clamped age always looks fresh, so the skew is read unclamped. The tolerated
        # skew is the one constant the writer and the risk gate already share.
        if source.is_future(self.max_future_skew_seconds, now):
            return FreshnessVerdict("future", None, stale_reason(source_class), source_class)
        age = source.age_seconds(now)
        if age > self.budget(source_class):
            return FreshnessVerdict("stale", age, stale_reason(source_class), source_class)
        return FreshnessVerdict("fresh", age, None, source_class)

    def price(
        self,
        price: Decimal | None,
        source: SourceRef | None,
        *,
        source_class: str = "mark",
        now: datetime | None = None,
    ) -> Decimal | None:
        """The price only when it is positive and its source is fresh; otherwise ``None``."""
        if price is None or price <= 0:
            return None
        if not self.classify(source, source_class=source_class, now=now).usable:
            return None
        return price


@dataclass(frozen=True)
class ProviderStaleAlert:
    """``alert.raised`` for a source that just went stale. One per transition."""

    entity: str
    source_class: str = "mark"
    severity: str = "warning"
    type: str = _PROVIDER_STALE
    dedupe_key: str = ""
    age_seconds: float | None = None
    observed_at: str | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "type": self.type,
            "entity": self.entity,
            "source_class": self.source_class,
            "dedupe_key": self.dedupe_key,
            "age_seconds": self.age_seconds,
            "observed_at": self.observed_at,
            "message": self.message,
        }


class SourceFreshness:
    """Track one source's freshness and raise ``provider_stale`` once per transition.

    ``observe`` is called on every read. It returns an alert only on the fresh→stale edge;
    a later read of the same outage returns ``None``, and a fresh observation closes the
    outage so the next one raises again. Unknown and future-dated observations count as
    stale: a source we cannot date is a source we cannot trust.
    """

    def __init__(self, policy: FreshnessPolicy | None = None) -> None:
        self._policy = policy or FreshnessPolicy()
        self._stale: dict[str, bool] = {}
        self._alerts: list[ProviderStaleAlert] = []

    def observe(
        self,
        entity: str,
        source: SourceRef | None,
        *,
        source_class: str = "mark",
        now: datetime | None = None,
    ) -> ProviderStaleAlert | None:
        current = now or utcnow()
        verdict = self._policy.classify(source, source_class=source_class, now=current)
        stale = not verdict.usable
        was_stale = self._stale.get(entity, False)
        self._stale[entity] = stale
        if not stale or was_stale:
            return None
        budget = self._policy.budget(source_class)
        if verdict.status == "unknown":
            message = f"{entity} has no observation; {source_class} budget {budget:g}s."
        elif verdict.status == "future":
            message = (
                f"{entity} is dated ahead of the clock; {source_class} read refused "
                f"(budget {budget:g}s)."
            )
        else:
            message = (
                f"{entity} is stale: age {verdict.age_seconds:g}s exceeds the "
                f"{source_class} budget {budget:g}s."
            )
        alert = ProviderStaleAlert(
            entity=entity,
            source_class=source_class,
            dedupe_key=f"{entity}:{_PROVIDER_STALE}",
            age_seconds=verdict.age_seconds,
            observed_at=None if source is None else _iso(source.observed_at),
            message=message,
        )
        self._alerts.append(alert)
        return alert

    def is_stale(self, entity: str) -> bool:
        return self._stale.get(entity, False)

    def alerts(self) -> tuple[ProviderStaleAlert, ...]:
        return tuple(self._alerts)

    def drain(self) -> tuple[ProviderStaleAlert, ...]:
        """Return every alert raised so far and forget it, for a publisher to deliver."""
        raised = tuple(self._alerts)
        self._alerts.clear()
        return raised
