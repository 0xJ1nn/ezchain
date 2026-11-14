"""Live pump.fun ingest: wrap a ``pumpfire`` client and feed the app's sinks.

The ``pumpfun-firehose`` package owns the socket, decoding and its monotonic
:class:`~pumpfire.health.StalenessGate`. This module is the app-side seam: it
registers on the client's listener, turns every raw pumpfire event into the one
core ``MarketEvent`` through the committed adapter, buffers the normalised
observation in the package's bounded :class:`~pumpfire.health.EventQueue`, and
hands a drained batch to the store (#281) or the JSONL replay log.

Liveness is tracked on decoded trade data, not on socket traffic.
:meth:`FirehoseSource.receive` normalises first and only then records the event
as proof of life; a payload the adapter rejects is counted and never refreshes
the gate. :meth:`FirehoseSource.pull` — the way an entry reaches the app — calls
``require_live`` and raises :class:`~pumpfire.health.StaleStream` when the last
decoded event is older than the budget, and then ages every drained observation
on its own handoff clock, refusing and counting the ones the stream can no longer
defend. A provider that only emits rejects therefore goes stale and serves
nothing. Freshness itself is never stored: the store keeps the observation and
the reader derives age, exactly as the contract requires.

The gate and the queue are injected through small protocols, so the entry rule
can be driven without the optional package installed (:class:`StreamGate`,
:class:`ObservationQueue`). The defaults are the package's own; nothing here
implements a second gate.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol, cast

from sqlalchemy.engine import Connection

from ezchain_adapters.firehose import (
    DEFAULT_STALE_BUDGET_SECONDS,
    FirehoseEvent,
    normalize_firehose_event,
)

from .eventlog import read_events, write_events
from .handoff import PendingObservation, split_stale
from .store import record_events

Listener = Callable[[object], None]

EXTRA = "ezchain-ingest[firehose]"


class GateDecisionLike(Protocol):
    """The slice of ``pumpfire.health.GateDecision`` this seam reads."""

    live: bool
    stale: bool
    age_s: float | None
    reason: str
    stale_after_s: float

    def as_dict(self) -> dict[str, Any]: ...


class StreamGate(Protocol):
    """The slice of ``pumpfire.health.StalenessGate`` this seam uses."""

    stale_after_s: float

    def observe(self, now: float | None = None) -> None: ...

    def check(self, now: float | None = None) -> GateDecisionLike: ...

    def require_live(self, now: float | None = None) -> GateDecisionLike: ...


class QueueCensusLike(Protocol):
    """The slice of ``pumpfire.health.QueueCensus`` this seam reports."""

    depth: int
    maxsize: int
    enqueued: int
    dropped: int

    def as_dict(self) -> dict[str, int]: ...


class ObservationQueue(Protocol):
    """A bounded lossy handoff of normalised observations."""

    def put_nowait(self, item: PendingObservation) -> bool: ...

    def get_nowait(self) -> PendingObservation: ...

    def empty(self) -> bool: ...

    def census(self) -> QueueCensusLike: ...


def _health() -> ModuleType:
    """Import ``pumpfire.health`` on first use. The package is an optional extra."""
    try:
        from pumpfire import health
    except ModuleNotFoundError as exc:
        if exc.name is None or not (exc.name == "pumpfire" or exc.name.startswith("pumpfire.")):
            raise
        raise ModuleNotFoundError(
            f"the live firehose source needs pumpfun-firehose; install {EXTRA}",
            name="pumpfire",
        ) from exc
    return cast(ModuleType, health)


class FirehoseClient(Protocol):
    """The slice of ``pumpfire.Firehose`` this seam uses; duck-typed for fakes."""

    def add_listener(self, callback: Callable[[object], object]) -> None: ...


class FirehoseSource:
    """Feed the app's ingest from a live pumpfire stream.

    ``client`` is any object with ``add_listener``; the real one is
    ``pumpfire.Firehose`` and the tests use a deterministic fake. Passing no
    client leaves the source buffer-only, which is what a caller that wires its
    own transport wants. ``gate`` and ``queue`` default to the package's own
    implementations; supplying both keeps the seam importable and testable
    without the optional extra.
    """

    def __init__(
        self,
        client: FirehoseClient | None = None,
        *,
        stale_after_s: float = DEFAULT_STALE_BUDGET_SECONDS,
        pending_limit: int = 10_000,
        gate: StreamGate | None = None,
        queue: ObservationQueue | None = None,
    ) -> None:
        if gate is None or queue is None:
            health = _health()
            if gate is None:
                gate = health.StalenessGate(stale_after_s)
            if queue is None:
                queue = health.EventQueue(pending_limit)
        self._gate = gate
        self._queue = queue
        self._received = 0
        self._rejected = 0
        self._refused_stale = 0
        self._client = client
        if client is not None:
            client.add_listener(self._listener)

    @property
    def gate(self) -> StreamGate:
        return self._gate

    def attach(self, client: FirehoseClient) -> FirehoseSource:
        """Subscribe to ``client``; returns the source for chaining."""
        client.add_listener(self._listener)
        self._client = client
        return self

    def _listener(self, raw: object) -> None:
        self.receive(raw)

    def receive(self, raw: object, *, now: float | None = None) -> FirehoseEvent | None:
        """Accept one decoded message and buffer its normalised observation.

        The payload is normalised first. Only a decoded event proves the trade
        feed is alive: ``None`` means the adapter rejected it (missing mint or
        timestamp, or an unknown kind), and a rejected payload is counted,
        never buffered, and never allowed to refresh the gate. Otherwise it is
        never invented.

        The handoff clock observed here is the same instant stamped on the
        buffered observation, so a later read can age that item against the same
        timebase the gate measures on.
        """
        event = normalize_firehose_event(raw)
        if event is None:
            self._rejected += 1
            return None
        stamp = time.monotonic() if now is None else now
        self._gate.observe(stamp)
        self._received += 1
        self._queue.put_nowait(PendingObservation(event, stamp))
        return event

    def pull(
        self, *, now: float | None = None, limit: int | None = None
    ) -> tuple[FirehoseEvent, ...]:
        """Drain buffered observations, refusing entries the stream cannot defend.

        Raises :class:`~pumpfire.health.StaleStream` — and drains nothing — when
        the gate is not live. Once it is live, every drained observation is still
        aged on its own handoff clock: an item older than the budget is refused
        and counted in ``refused_stale``, so a recovered feed never serves the
        backlog that accrued during the gap.
        """
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")
        self._gate.require_live(now)
        clock = time.monotonic() if now is None else now
        drained: list[PendingObservation] = []
        while not self._queue.empty() and (limit is None or len(drained) < limit):
            drained.append(self._queue.get_nowait())
        entries, refused = split_stale(drained, now=clock, stale_after_s=self._gate.stale_after_s)
        self._refused_stale += refused
        return tuple(entries)

    def require_live(self, now: float | None = None) -> GateDecisionLike:
        """The gate's decision, or ``StaleStream``. Callers gating entries use this."""
        return self._gate.require_live(now)

    def record(
        self,
        connection: Connection,
        *,
        now: float | None = None,
        outbox: bool = True,
    ) -> int:
        """Persist the pulled batch through ``record_events``; return rows written.

        A stale gate raises before anything is read out of the buffer, so the
        transaction is never opened with entries the stream cannot defend.
        """
        events = self.pull(now=now)
        return record_events(connection, (event.event for event in events), outbox=outbox)

    def append_log(self, path: str | Path, *, now: float | None = None) -> int:
        """Append the pulled batch to a JSONL recording; return lines appended.

        The recording keeps the contract's serialiser, so it carries the
        observation and no freshness verdict.
        """
        events = tuple(event.event for event in self.pull(now=now))
        existing = read_events(path) if Path(path).exists() else ()
        write_events(path, (*existing, *events))
        return len(events)

    def health(self, now: float | None = None) -> dict[str, Any]:
        """One snapshot of the gate plus this source's counters.

        The gate supplies the last-event age and the live/stale verdict, the queue
        census supplies depth and drops, the decode ratio is this source's own
        received/rejected pair, and ``refused_stale`` counts the observations the
        entry read discarded on their own age. An unobserved stream reports
        ``None`` for the ratio rather than a perfect 1.0.
        """
        decision = self._gate.check(now)
        census = self._queue.census()
        decided = self._received + self._rejected
        return {
            "gate": decision.as_dict(),
            "queue": census.as_dict(),
            "received": self._received,
            "rejected": self._rejected,
            "dropped": census.dropped,
            "refused_stale": self._refused_stale,
            "decoded_ratio": None if decided == 0 else self._received / decided,
            "rejected_ratio": None if decided == 0 else self._rejected / decided,
        }

    def census(self) -> QueueCensusLike:
        return self._queue.census()


__all__ = [
    "FirehoseClient",
    "FirehoseSource",
    "GateDecisionLike",
    "ObservationQueue",
    "QueueCensusLike",
    "StreamGate",
]
