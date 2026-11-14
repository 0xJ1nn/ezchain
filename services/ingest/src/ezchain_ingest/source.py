"""Live pump.fun ingest: wrap a ``pumpfire`` client and feed the app's sinks.

The ``pumpfun-firehose`` package owns the socket, decoding and its monotonic
:class:`~pumpfire.health.StalenessGate`. This module is the app-side seam: it
registers on the client's listener, turns every raw pumpfire event into the one
core ``MarketEvent`` through the committed adapter, buffers the normalised
observation in the package's bounded :class:`~pumpfire.health.EventQueue`, and
hands a drained batch to the store (#281) or the JSONL replay log.

The gate is the package's, not a second one. :meth:`FirehoseSource.receive`
records a decoded message as proof of life; :meth:`FirehoseSource.pull` — the
way an entry reaches the app — calls ``require_live`` and raises
:class:`~pumpfire.health.StaleStream` when the last message is older than the
budget, so a stale feed yields no entries at all and the sinks stay untouched.
Freshness itself is never stored: the store keeps the observation and the
reader derives age, exactly as the contract requires.

``chain`` is data here. Nothing in this module branches on a chain or venue
name; a source is chosen by capability, not by a chain literal.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from pumpfire.health import (
    EventQueue,
    GateDecision,
    QueueCensus,
    StalenessGate,
)
from sqlalchemy.engine import Connection

from ezchain_adapters.firehose import (
    DEFAULT_STALE_BUDGET_SECONDS,
    FirehoseEvent,
    normalize_firehose_event,
)

from .eventlog import read_events, write_events
from .store import record_events

Listener = Callable[[object], None]


class FirehoseClient(Protocol):
    """The slice of ``pumpfire.Firehose`` this seam uses; duck-typed for fakes."""

    def add_listener(self, callback: Callable[[object], object]) -> None: ...


class FirehoseSource:
    """Feed the app's ingest from a live pumpfire stream.

    ``client`` is any object with ``add_listener``; the real one is
    ``pumpfire.Firehose`` and the tests use a deterministic fake. Passing no
    client leaves the source buffer-only, which is what a caller that wires its
    own transport wants.
    """

    def __init__(
        self,
        client: FirehoseClient | None = None,
        *,
        stale_after_s: float = DEFAULT_STALE_BUDGET_SECONDS,
        pending_limit: int = 10_000,
        gate: StalenessGate | None = None,
    ) -> None:
        self._gate = StalenessGate(stale_after_s) if gate is None else gate
        self._queue: EventQueue[FirehoseEvent] = EventQueue(pending_limit)
        self._received = 0
        self._rejected = 0
        self._client = client
        if client is not None:
            client.add_listener(self._listener)

    @property
    def gate(self) -> StalenessGate:
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

        A message is proof of life whatever it decodes to, so the gate is
        refreshed first — the same rule the package's reader applies. ``None``
        means the adapter rejected the payload (missing mint or timestamp, or an
        unknown kind); it is counted, never buffered, and never invented.
        """
        self._gate.observe(now)
        event = normalize_firehose_event(raw)
        if event is None:
            self._rejected += 1
            return None
        self._received += 1
        self._queue.put_nowait(event)
        return event

    def pull(
        self, *, now: float | None = None, limit: int | None = None
    ) -> tuple[FirehoseEvent, ...]:
        """Drain buffered observations, refusing entries while the stream is stale.

        Raises :class:`~pumpfire.health.StaleStream` — and drains nothing — when
        the gate is not live, so a gap cannot leak an entry into a sink.
        """
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")
        self._gate.require_live(now)
        entries: list[FirehoseEvent] = []
        while not self._queue.empty() and (limit is None or len(entries) < limit):
            entries.append(self._queue.get_nowait())
        return tuple(entries)

    def require_live(self, now: float | None = None) -> GateDecision:
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
        """One snapshot of the package's gate plus this source's counters."""
        decision = self._gate.check(now)
        census = self._queue.census()
        return {
            "gate": decision.as_dict(),
            "queue": census.as_dict(),
            "received": self._received,
            "rejected": self._rejected,
        }

    def census(self) -> QueueCensus:
        return self._queue.census()


__all__ = ["FirehoseClient", "FirehoseSource"]
