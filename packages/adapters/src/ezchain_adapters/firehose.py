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

A gap is a first-class state, because a WebSocket reconnects by nature.
``FirehoseFeed.entries`` refuses while the newest observation is older than the
budget (``StaleFeed``), so a stale feed cannot leak an entry into an order, and
``FirehoseFeed.health`` reports the four facts a dashboard reads — last-event age,
reconnect count, drop count and the decoded/rejected ratio. The handoff to a
consumer is a bounded buffer that drops the oldest observation under pressure and
counts it; its census is synchronous, so observing the queue never blocks the
reader. These are the same three pieces the upstream package owns
(``pumpfire.health``); this module needs no dependency on it, and
``services/ingest`` uses the package's copy for the live socket.

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
from collections import deque
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


class StaleFeed(RuntimeError):
    """Raised when an entry is requested from a feed whose last event is too old.

    A gap is a state, not an error to retry: the refusal carries the age that
    caused it and the budget it broke, so a caller reports a number and a verdict
    instead of an empty list that looks like a quiet market. Nothing is drained
    by the refusal — the observations stay buffered for a live reader.
    """

    def __init__(
        self,
        *,
        age_seconds: float | None,
        stale_budget_seconds: float,
        observed_at: str | None = None,
    ) -> None:
        self.age_seconds = age_seconds
        self.stale_budget_seconds = stale_budget_seconds
        self.observed_at = observed_at
        detail = (
            "no event observed yet" if age_seconds is None else f"last event age {age_seconds:g}s"
        )
        super().__init__(
            f"{SOURCE_NAME} feed is not live; {detail}, budget {stale_budget_seconds:g}s"
        )

    @property
    def stale(self) -> bool:
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": SOURCE_NAME,
            "stale": True,
            "age_seconds": self.age_seconds,
            "last_event_at": self.observed_at,
            "stale_budget_seconds": self.stale_budget_seconds,
            "type": "provider_stale",
        }


class _EventBuffer:
    """A bounded handoff from the wire to a consumer: newest wins, oldest is dropped.

    ``put`` never waits. When the buffer is full the oldest observation is
    discarded first, so the socket keeps up at the cost of the stale end of the
    backlog, and every discard is counted. ``census`` is synchronous, so reading
    depth and counters never blocks the reader that is falling behind.
    """

    __slots__ = ("dropped", "enqueued", "maxsize", "_items", "_waiter")

    def __init__(self, maxsize: int) -> None:
        if maxsize < 1:
            raise ValueError("queue_size must be at least 1")
        self.maxsize = int(maxsize)
        self.enqueued = 0
        self.dropped = 0
        self._items: deque[FirehoseEvent] = deque()
        self._waiter: asyncio.Event | None = None

    def put(self, event: FirehoseEvent) -> None:
        if len(self._items) >= self.maxsize:
            self._items.popleft()
            self.dropped += 1
        self._items.append(event)
        self.enqueued += 1
        if self._waiter is not None:
            self._waiter.set()

    def take(self) -> FirehoseEvent | None:
        return self._items.popleft() if self._items else None

    def drain(self) -> list[FirehoseEvent]:
        out = list(self._items)
        self._items.clear()
        return out

    def census(self) -> dict[str, int]:
        return {
            "depth": len(self._items),
            "maxsize": self.maxsize,
            "enqueued": self.enqueued,
            "dropped": self.dropped,
        }

    def open_waiter(self) -> asyncio.Event:
        self._waiter = asyncio.Event()
        return self._waiter

    def close_waiter(self) -> None:
        self._waiter = None


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
    ``provider_stale`` alert per stale transition, refuses every entry read
    while the feed is stale (:class:`StaleFeed`), and exposes a synchronous
    health snapshot. The handoff to a consumer is a bounded buffer that drops
    the oldest observation under pressure and counts it, so a slow reader never
    stalls the socket. Not wired into the web app; data layer only.
    """

    def __init__(
        self,
        *,
        stale_budget_seconds: float = DEFAULT_STALE_BUDGET_SECONDS,
        queue_size: int = MAX_RECENT,
    ) -> None:
        if stale_budget_seconds <= 0:
            raise ValueError("stale_budget_seconds must be positive")
        self._budget = float(stale_budget_seconds)
        self._last_seen: datetime | None = None
        self._stale_active = False
        self._alerts: list[FirehoseAlert] = []
        self._recent: list[FirehoseEvent] = []
        self._by_pair: dict[str, FirehoseEvent] = {}
        self._seen: dict[str, None] = {}
        self._buffer = _EventBuffer(queue_size)
        self._decoded = 0
        self._rejected = 0
        self._duplicates = 0
        self._reconnects = 0

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
        self._buffer.put(event)
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

    def census(self) -> dict[str, int]:
        """Depth, capacity and drop count. Synchronous: it never awaits the reader."""
        return self._buffer.census()

    def entries(self, now: datetime | None = None) -> tuple[FirehoseEvent, ...]:
        """Drain the buffer for an entry decision, refusing while the feed is stale.

        This is the one entry read. A gap raises :class:`StaleFeed` and drains
        nothing, so an untradeable feed cannot leak an observation into an order.
        """
        current = now or utcnow()
        age, stale = self._check_staleness(current)
        if stale:
            raise StaleFeed(
                age_seconds=age,
                stale_budget_seconds=self._budget,
                observed_at=None if self._last_seen is None else self._last_seen.isoformat(),
            )
        return tuple(self._buffer.drain())

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
        """One snapshot: age, stale verdict, reconnects, drops and the decode ratio.

        Every number here is measured. An unobserved feed reports ``None`` for
        its age and both ratios rather than a fresh-looking zero or a perfect 1.0.
        """
        current = now or utcnow()
        age, stale = self._check_staleness(current)
        decided = self._decoded + self._rejected
        queue = self._buffer.census()
        return {
            "source": SOURCE_NAME,
            "stale": stale,
            "last_event_age_seconds": age,
            "last_event_at": None if self._last_seen is None else self._last_seen.isoformat(),
            "stale_budget_seconds": self._budget,
            "decoded": self._decoded,
            "rejected": self._rejected,
            "duplicates": self._duplicates,
            "reconnects": self._reconnects,
            "decoded_ratio": None if decided == 0 else self._decoded / decided,
            "rejected_ratio": None if decided == 0 else self._rejected / decided,
            "dropped": queue["dropped"],
            "queue": queue,
            "alerts": [alert.to_dict() for alert in self._alerts],
        }

    def fresh_events(self, now: datetime | None = None) -> tuple[FirehoseEvent, ...]:
        """The recent observations, or nothing while stale. Read-only: drains nothing."""
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
        # The snapshot carries every observation buffered so far, so deltas are what
        # arrives after the snapshot. No await sits between the two, so nothing is lost.
        opening = self.snapshot(now)
        self._buffer.drain()
        waiter = self._buffer.open_waiter()
        try:
            yield opening
            while True:
                event = self._buffer.take()
                if event is None:
                    waiter.clear()
                    # A put between the take and the clear is not lost: re-check depth.
                    if self._buffer.census()["depth"] == 0:
                        await waiter.wait()
                    continue
                yield {"type": "delta", "event": event.to_dict(event.observed_at)}
        finally:
            self._buffer.close_waiter()


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
    "StaleFeed",
    "normalize_firehose_event",
]
