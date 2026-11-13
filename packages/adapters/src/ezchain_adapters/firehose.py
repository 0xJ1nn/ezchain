"""Pumpfun-firehose read-only adapter: ``pumpfun-firehose`` events to ``MarketEvent``.

Consumes the published ``pumpfun-firehose`` package (MIT, pinned ``0.4.0``,
``https://github.com/0xJ1nn/pumpfun-firehose``) as the pump.fun market source.
The adapter is read-only everywhere: it never signs, never trades, never places
an order. Tests replay recorded events offline; no test reaches the network.

The adapter normalises into the one ``MarketEvent`` contract in ``ezchain_core``;
this module defines no second market event. What stays local is this wire's own
shape: ``FirehoseEvent`` carries the contract observation plus the pump.fun extras
(kind, signature, slot, token metadata) the contract deliberately does not hold,
and its ``to_dict`` composes the contract's serialiser rather than writing a
second one. Age is computed at read time from ``source.observed_at`` (never
stamped at write time), and an event without a defensible source timestamp is
dropped (fail closed, I3). ``chain`` is data here; nothing branches on it (I6).

PyPI does not publish ``pumpfun-firehose`` in this environment (``pip index``
reports no versions; GitHub carries no ``0.4.0`` tag), so the adapter treats
the dependency as duck-typed: it accepts both ``pumpfire`` dataclass objects
(``TradeEvent``/``CreateEvent``/``CompleteEvent``) and their ``as_dict()``
mappings, and works with either when the package is installed. The pinned
version is recorded in :data:`PINNED_VERSION` for provenance; no ezCHAIN type
is imported by the package and none of its code is vendored here.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from ezchain_core import (
    DEFAULT_STALENESS_SECONDS,
    Direction,
    MarketEvent,
    MarketEventError,
    SourceRef,
    utcnow,
)

SOURCE_NAME = "pumpfun-firehose"
SOURCE_URL = "https://github.com/0xJ1nn/pumpfun-firehose"
CHAIN = "solana"
UNITS = "SOL"
PINNED_VERSION = "0.4.0"
DEFAULT_STALE_BUDGET_SECONDS = DEFAULT_STALENESS_SECONDS
MAX_RECENT = 500
DEDUPE_WINDOW_LIMIT = 250_000

EventKind = Literal["create", "trade", "complete"]


def _get(raw: object, key: str, default: Any = None) -> Any:
    if isinstance(raw, Mapping):
        return raw.get(key, default)
    return getattr(raw, key, default)


def _text(value: object, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value
    return default


def _optional_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _dec(value: object) -> Decimal | None:
    """Parse a finite Decimal or return ``None``. Unknown is never zero."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _positive_dec(value: object) -> Decimal | None:
    """A usable quantity: finite and greater than zero, else unknown."""
    parsed = _dec(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _slot(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _timestamp(value: object) -> tuple[int, datetime] | None:
    """Return ``(epoch, observed_at)`` only for a defensible source timestamp.

    The firehose ``timestamp`` is an ``int`` of unix seconds from the chain
    event. Anything else (missing, float, string, bool, zero, negative,
    out-of-range) cannot be defended and the event is dropped.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value <= 0:
        return None
    try:
        observed = datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
    return value, observed


def _direction(raw: object) -> Direction | None:
    """Trade side from the wire's ``is_buy`` flag, falling back to ``side``."""
    is_buy = _get(raw, "is_buy")
    if isinstance(is_buy, bool):
        return "buy" if is_buy else "sell"
    side = _get(raw, "side")
    if side == "buy":
        return "buy"
    if side == "sell":
        return "sell"
    return None


@dataclass(frozen=True)
class FirehoseEvent:
    """One firehose event: the contract observation plus this wire's extras.

    ``event`` is the one normalised ``MarketEvent``. Its fields are forwarded as
    properties so a consumer reads ``pair``/``price``/``size``/``direction``
    through the same names every other source uses.
    """

    kind: EventKind
    event: MarketEvent
    timestamp: int
    signature: str = ""
    slot: int | None = None
    size_tokens: Decimal | None = None
    fee_sol: Decimal | None = None
    user: str | None = None
    creator: str | None = None
    bonding_curve: str | None = None
    pool: str | None = None
    symbol: str | None = None
    name: str | None = None
    uri: str | None = None

    @property
    def chain(self) -> str:
        return self.event.chain

    @property
    def venue(self) -> str:
        return self.event.venue

    @property
    def pair(self) -> str:
        return self.event.pair

    @property
    def price(self) -> Decimal | None:
        return self.event.price

    @property
    def size(self) -> Decimal | None:
        return self.event.size

    @property
    def direction(self) -> Direction | None:
        return self.event.direction

    @property
    def units(self) -> str:
        return self.event.units

    @property
    def source(self) -> SourceRef:
        return self.event.source

    @property
    def observed_at(self) -> datetime:
        return self.event.observed_at

    def age_seconds(self, now: datetime | None = None) -> float:
        return self.event.age_seconds(now)

    def is_stale(
        self,
        max_age_seconds: float = DEFAULT_STALE_BUDGET_SECONDS,
        now: datetime | None = None,
    ) -> bool:
        return self.event.is_stale(max_age_seconds, now)

    def to_dict(self, now: datetime | None = None) -> dict[str, Any]:
        """The contract's serialiser, widened with the wire extras."""
        body = self.event.to_dict(now)
        body.update(
            {
                "kind": self.kind,
                "mint": self.event.pair,
                "signature": self.signature,
                "slot": self.slot,
                "timestamp": self.timestamp,
                "size_tokens": None if self.size_tokens is None else str(self.size_tokens),
                "fee_sol": None if self.fee_sol is None else str(self.fee_sol),
                "user": self.user,
                "creator": self.creator,
                "bonding_curve": self.bonding_curve,
                "pool": self.pool,
                "symbol": self.symbol,
                "name": self.name,
                "uri": self.uri,
            }
        )
        return body


@dataclass(frozen=True)
class FirehoseAlert:
    """``alert.raised`` for a stale feed, emitted once per transition."""

    severity: str = "warning"
    type: str = "provider_stale"
    entity: str = SOURCE_NAME
    dedupe_key: str = f"{SOURCE_NAME}:provider_stale"
    age_seconds: float | None = None
    observed_at: str | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "type": self.type,
            "entity": self.entity,
            "dedupe_key": self.dedupe_key,
            "age_seconds": self.age_seconds,
            "observed_at": self.observed_at,
            "message": self.message,
        }


def normalize_firehose_event(raw: object) -> FirehoseEvent | None:
    """Normalize one firehose event (mapping or ``pumpfire`` object) to the contract.

    Returns ``None`` without mint, with an unknown kind, without a defensible
    timestamp, or when the contract refuses the observation — no event passes
    without a timestamp it can defend. The adapter has no clock of its own, so the
    chain's timestamp is both the source clock and ``observed_at``.
    """
    kind_raw = _get(raw, "kind")
    if kind_raw not in ("create", "trade", "complete"):
        return None
    kind: EventKind = kind_raw
    mint = _text(_get(raw, "mint"), "")
    if not mint:
        return None
    stamped = _timestamp(_get(raw, "timestamp"))
    if stamped is None:
        return None
    timestamp, observed_at = stamped
    venue = "pumpswap" if kind == "trade" and _get(raw, "venue") == "pumpswap" else "pumpfun"

    direction: Direction | None = None
    price: Decimal | None = None
    size: Decimal | None = None
    size_tokens: Decimal | None = None
    fee_sol: Decimal | None = None

    if kind == "trade":
        direction = _direction(raw)
        price = _positive_dec(_get(raw, "price_sol"))
        size = _positive_dec(_get(raw, "sol"))
        size_tokens = _positive_dec(_get(raw, "tokens"))
        fee_sol = _positive_dec(_get(raw, "fee_sol"))
    else:
        virtual_sol = _positive_dec(_get(raw, "virtual_sol"))
        virtual_token = _positive_dec(_get(raw, "virtual_token"))
        if virtual_sol is not None and virtual_token is not None:
            price = virtual_sol / virtual_token

    try:
        event = MarketEvent(
            chain=CHAIN,
            venue=venue,
            pair=mint,
            price=price,
            size=size,
            direction=direction,
            units=UNITS,
            source=SourceRef(SOURCE_NAME, SOURCE_URL, observed_at),
            observed_at=observed_at,
        )
    except MarketEventError:
        return None

    return FirehoseEvent(
        kind=kind,
        event=event,
        timestamp=timestamp,
        signature=_text(_get(raw, "signature"), ""),
        slot=_slot(_get(raw, "slot")),
        size_tokens=size_tokens,
        fee_sol=fee_sol,
        user=_optional_text(_get(raw, "user")),
        creator=_optional_text(_get(raw, "creator")),
        bonding_curve=_optional_text(_get(raw, "bonding_curve")),
        pool=_optional_text(_get(raw, "pool")),
        symbol=_optional_text(_get(raw, "symbol")),
        name=_optional_text(_get(raw, "name")),
        uri=_optional_text(_get(raw, "uri")),
    )


class FirehoseFeed:
    """In-memory read-only feed over normalized events.

    Tracks last-event age against a staleness budget, emits one
    ``provider_stale`` alert per stale transition, degrades reads while stale
    (``fresh_events`` returns nothing), and exposes snapshot+deltas for the
    API surface. Not wired into the web app; data layer only.
    """

    def __init__(self, *, stale_budget_seconds: float = DEFAULT_STALE_BUDGET_SECONDS) -> None:
        if stale_budget_seconds <= 0:
            raise ValueError("stale_budget_seconds must be positive")
        self._budget = float(stale_budget_seconds)
        self._last_seen: datetime | None = None
        self._stale_active = False
        self._alerts: list[FirehoseAlert] = []
        self._recent: list[FirehoseEvent] = []
        self._by_pair: dict[str, FirehoseEvent] = {}
        self._seen: dict[str, None] = {}
        self._decoded = 0
        self._rejected = 0
        self._duplicates = 0
        self._reconnects = 0
        self._queue: asyncio.Queue[FirehoseEvent] | None = None

    def ingest(self, raw: object) -> FirehoseEvent | None:
        event = normalize_firehose_event(raw)
        if event is None:
            self._rejected += 1
            return None
        if event.signature and event.signature in self._seen:
            self._duplicates += 1
            return None
        if event.signature:
            self._seen[event.signature] = None
            while len(self._seen) > DEDUPE_WINDOW_LIMIT:
                self._seen.pop(next(iter(self._seen)))
        self._decoded += 1
        self._recent.append(event)
        if len(self._recent) > MAX_RECENT:
            self._recent = self._recent[-MAX_RECENT:]
        self._by_pair[event.pair] = event
        if self._last_seen is None or event.observed_at > self._last_seen:
            self._last_seen = event.observed_at
        self._stale_active = False
        if self._queue is not None:
            try:
                self._queue.put_nowait(event)
            except asyncio.QueueFull:  # keep the reader bounded; drop oldest
                self._recent = self._recent[-MAX_RECENT:]
        return event

    def replay(self, raw_events: Iterable[object]) -> list[FirehoseEvent]:
        out: list[FirehoseEvent] = []
        for raw in raw_events:
            event = self.ingest(raw)
            if event is not None:
                out.append(event)
        return out

    def record_reconnect(self) -> None:
        self._reconnects += 1

    def _check_staleness(self, now: datetime) -> tuple[float | None, bool]:
        if self._last_seen is None:
            age: float | None = None
            stale = True
        else:
            age = max(0.0, (now - self._last_seen).total_seconds())
            stale = age > self._budget
        if stale and not self._stale_active:
            observed = None if self._last_seen is None else self._last_seen.isoformat()
            if age is None:
                message = f"{SOURCE_NAME} feed has no observed event; budget {self._budget:g}s."
            else:
                message = (
                    f"{SOURCE_NAME} feed is stale: last event age "
                    f"{age:g}s exceeds budget {self._budget:g}s."
                )
            self._alerts.append(
                FirehoseAlert(age_seconds=age, observed_at=observed, message=message)
            )
            self._stale_active = True
        elif not stale:
            self._stale_active = False
        return age, stale

    def health(self, now: datetime | None = None) -> dict[str, Any]:
        current = now or utcnow()
        age, stale = self._check_staleness(current)
        denominator = self._decoded + self._rejected
        ratio = (self._decoded / denominator) if denominator > 0 else 1.0
        return {
            "source": SOURCE_NAME,
            "stale": stale,
            "last_event_age_seconds": age,
            "stale_budget_seconds": self._budget,
            "decoded": self._decoded,
            "rejected": self._rejected,
            "duplicates": self._duplicates,
            "reconnects": self._reconnects,
            "decoded_ratio": ratio,
            "alerts": [alert.to_dict() for alert in self._alerts],
        }

    def fresh_events(self, now: datetime | None = None) -> tuple[FirehoseEvent, ...]:
        current = now or utcnow()
        _age, stale = self._check_staleness(current)
        if stale:
            return ()
        return tuple(self._recent)

    def snapshot(self, now: datetime | None = None) -> dict[str, Any]:
        current = now or utcnow()
        health = self.health(current)
        coins = [event.to_dict(current) for event in self._by_pair.values()]
        recent = [event.to_dict(current) for event in self._recent]
        return {
            "type": "snapshot",
            "source": SourceRef(SOURCE_NAME, SOURCE_URL, current).to_dict(),
            "stale": health["stale"],
            "age_seconds": health["last_event_age_seconds"],
            "health": health,
            "alerts": health["alerts"],
            "coins": coins,
            "recent": recent,
        }

    async def stream(self, now: datetime | None = None) -> AsyncIterator[dict[str, Any]]:
        """Snapshot then deltas. The API surface subscribes here; the web app does not."""
        queue: asyncio.Queue[FirehoseEvent] = asyncio.Queue(maxsize=MAX_RECENT)
        self._queue = queue
        try:
            yield self.snapshot(now)
            while True:
                event = await queue.get()
                yield {"type": "delta", "event": event.to_dict(event.observed_at)}
        finally:
            self._queue = None


__all__ = [
    "CHAIN",
    "DEFAULT_STALE_BUDGET_SECONDS",
    "MAX_RECENT",
    "PINNED_VERSION",
    "SOURCE_NAME",
    "SOURCE_URL",
    "UNITS",
    "FirehoseAlert",
    "FirehoseEvent",
    "FirehoseFeed",
    "MarketEvent",
    "normalize_firehose_event",
]
