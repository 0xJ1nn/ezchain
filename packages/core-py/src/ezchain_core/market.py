"""One normalised market observation: the canonical ``MarketEvent`` contract.

Every market source — a chain stream, a venue REST poll, a candle writer — normalises
to this one dataclass, one schema version and one serialiser. The fields are the
observation, its provenance and its unit; freshness is never a stored field. Age and
staleness are computed from the caller's clock when read, so a recorded observation
replays identically regardless of when it is read (CONTRACT I9).

``chain`` is ordinary data here. Core never branches on it or on a chain-name literal
(CONTRACT I6); a chain's capabilities belong to its adapter.

Unknown is never zero: a missing price, size or confidence stays ``None`` and is
rejected rather than coerced on replay. A zero or negative price is an unusable market
observation, so it is refused at construction.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from .types import SourceRef

MARKET_EVENT_SCHEMA_VERSION = 1
DEFAULT_STALENESS_SECONDS = 60.0
Direction = Literal["buy", "sell"]


class MarketEventError(ValueError):
    """A market observation cannot be trusted or replayed."""


def _aware(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _parse_time(value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        return _aware(value)
    if isinstance(value, str):
        try:
            return _aware(datetime.fromisoformat(value))
        except ValueError as exc:
            raise MarketEventError(f"{field} is not an ISO-8601 timestamp") from exc
    raise MarketEventError(f"{field} must be an ISO-8601 timestamp or datetime")


def _parse_decimal(value: object, field: str) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise MarketEventError(f"{field} must be a decimal, not {type(value).__name__}")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MarketEventError(f"{field} must be a finite decimal") from exc
    if not parsed.is_finite():
        raise MarketEventError(f"{field} must be a finite decimal")
    return parsed


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MarketEventError(f"{field} must be a non-empty string")
    return value


@dataclass(frozen=True)
class MarketEvent:
    """One observation of a market, normalised from any source.

    ``source.observed_at`` is the source's own timestamp (when the chain or venue says
    the observation happened); ``observed_at`` is the ingest clock. ``age_seconds`` and
    ``is_stale`` read the former, so the staleness verdict belongs to the reader, not
    to the writer. ``ingest_lag_seconds`` is the gap between the two clocks.
    """

    chain: str
    venue: str
    pair: str
    price: Decimal | None
    size: Decimal | None
    direction: Direction | None
    units: str
    source: SourceRef
    observed_at: datetime
    confidence: Decimal | None = None
    schema_version: int = MARKET_EVENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or self.schema_version != MARKET_EVENT_SCHEMA_VERSION
        ):
            raise MarketEventError(
                f"schema version must be {MARKET_EVENT_SCHEMA_VERSION}, got {self.schema_version!r}"
            )
        for name in ("chain", "venue", "pair"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise MarketEventError(f"{name} must be a non-empty string")
        if not isinstance(self.units, str) or not self.units.strip():
            raise MarketEventError("units must be declared")
        if not isinstance(self.source, SourceRef) or not self.source.name.strip():
            raise MarketEventError("source must carry a non-empty name")
        if self.direction not in (None, "buy", "sell"):
            raise MarketEventError("direction must be buy, sell or unknown")
        if not isinstance(self.observed_at, datetime):
            raise MarketEventError("observed_at must be a datetime")
        object.__setattr__(self, "observed_at", _aware(self.observed_at))
        object.__setattr__(self, "source", _normalise_source(self.source))
        object.__setattr__(self, "price", _require_decimal(self.price, "price", positive=True))
        object.__setattr__(self, "size", _require_decimal(self.size, "size", positive=True))
        object.__setattr__(
            self, "confidence", _require_decimal(self.confidence, "confidence", positive=False)
        )
        if self.confidence is not None and self.confidence > 1:
            raise MarketEventError("confidence must be between 0 and 1")

    def age_seconds(self, now: datetime | None = None) -> float:
        """Age of the source observation at read time, never stored."""
        return self.source.age_seconds(now)

    def ingest_lag_seconds(self) -> float:
        """Clock gap between the source timestamp and the ingest observation."""
        return (self.observed_at - self.source.observed_at).total_seconds()

    def is_stale(
        self,
        max_age_seconds: float = DEFAULT_STALENESS_SECONDS,
        now: datetime | None = None,
    ) -> bool:
        return self.age_seconds(now) > max_age_seconds

    def to_dict(self, now: datetime | None = None) -> dict[str, Any]:
        """Serialise one observation.

        With no ``now`` the output is the recorded form: no freshness verdict. Passing
        ``now`` adds the read-time ``age_seconds`` and ``stale`` fields for a consumer
        that is displaying the observation, and never changes the recorded fields.
        """
        body: dict[str, Any] = {
            "schema_version": self.schema_version,
            "chain": self.chain,
            "venue": self.venue,
            "pair": self.pair,
            "price": None if self.price is None else str(self.price),
            "size": None if self.size is None else str(self.size),
            "direction": self.direction,
            "units": self.units,
            "confidence": None if self.confidence is None else str(self.confidence),
            "source": self.source.to_dict(),
            "source_timestamp": self.source.observed_at.isoformat(),
            "observed_at": self.observed_at.isoformat(),
        }
        if now is not None:
            body["age_seconds"] = self.age_seconds(now)
            body["stale"] = self.is_stale(DEFAULT_STALENESS_SECONDS, now)
        return body

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MarketEvent:
        """Rebuild one recorded observation; derived freshness fields are ignored."""
        if not isinstance(data, Mapping):
            raise MarketEventError("a market record must be a mapping")
        version = data.get("schema_version")
        if isinstance(version, bool) or version != MARKET_EVENT_SCHEMA_VERSION:
            raise MarketEventError(
                f"schema version must be {MARKET_EVENT_SCHEMA_VERSION}, got {version!r}"
            )
        raw_source = data.get("source")
        if not isinstance(raw_source, Mapping):
            raise MarketEventError("source must be a mapping")
        source_name = _text(raw_source.get("name"), "source name")
        raw_url = raw_source.get("url")
        if raw_url is not None and not isinstance(raw_url, str):
            raise MarketEventError("source url must be a string or null")
        source_at = _parse_time(raw_source.get("observed_at"), "source observed_at")
        source = SourceRef(source_name, raw_url, source_at)

        source_timestamp = data.get("source_timestamp")
        if (
            source_timestamp is not None
            and _parse_time(source_timestamp, "source_timestamp") != source_at
        ):
            raise MarketEventError("source_timestamp must match source.observed_at")

        direction = data.get("direction")
        if direction not in (None, "buy", "sell"):
            raise MarketEventError("direction must be buy, sell or unknown")

        return cls(
            chain=_text(data.get("chain"), "chain"),
            venue=_text(data.get("venue"), "venue"),
            pair=_text(data.get("pair"), "pair"),
            price=_parse_decimal(data.get("price"), "price"),
            size=_parse_decimal(data.get("size"), "size"),
            direction=direction,
            units=_text(data.get("units"), "units"),
            source=source,
            observed_at=_parse_time(data.get("observed_at"), "observed_at"),
            confidence=_parse_decimal(data.get("confidence"), "confidence"),
            schema_version=MARKET_EVENT_SCHEMA_VERSION,
        )


def _normalise_source(source: SourceRef) -> SourceRef:
    if not isinstance(source, SourceRef):
        raise MarketEventError("source must be a SourceRef")
    return SourceRef(source.name, source.url, _aware(source.observed_at))


def _require_decimal(value: object, field: str, *, positive: bool) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, Decimal):
        raise MarketEventError(f"{field} must be Decimal or null")
    if not value.is_finite():
        raise MarketEventError(f"{field} must be a finite decimal")
    if positive and value <= 0:
        raise MarketEventError(f"{field} must be greater than zero")
    if not positive and value < 0:
        raise MarketEventError(f"{field} cannot be negative")
    return value


__all__ = [
    "DEFAULT_STALENESS_SECONDS",
    "Direction",
    "MARKET_EVENT_SCHEMA_VERSION",
    "MarketEvent",
    "MarketEventError",
]
