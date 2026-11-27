"""Resumable, scoped fan-out of a session's append-only trace.

The runtime writes the trace; this module only tails it. A channel holds the events already
appended to one session and the subscribers waiting for the next one. A client that reconnects
names the last event id it saw and receives only what it missed, in order. Nothing here can
rewrite, drop or reorder a trace event, so the stream is a view of the trace, never a second
source of truth.
"""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ezchain_agent_runtime import TraceEvent
from ezchain_agent_runtime.trace import (
    MemoryTraceLog,
    TraceEventKind,
    TraceSink,
    TraceStoreError,
    TraceTail,
)

from .api_keys import Principal

STREAM_PREFIX = "/api/v1/stream"

# A channel keeps a bounded tail, not the whole trace: the store still owns the full history.
# A subscriber that stops reading is dropped once its queue fills, and the number of live
# channels is capped so a long-lived process cannot accumulate one per session ever seen.
MAX_CHANNEL_EVENTS = 512
MAX_SUBSCRIBER_QUEUE = 256
MAX_CHANNELS = 128


class StreamError(Exception):
    """A typed reason a stream could not be opened."""

    code = "STREAM_ERROR"
    status_code = 400
    retryable = False

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


class UnknownSession(StreamError):
    code = "STREAM_SESSION_NOT_FOUND"
    status_code = 404


class ScopeDenied(StreamError):
    code = "STREAM_SCOPE_DENIED"
    status_code = 403


class PrincipalMismatch(StreamError):
    code = "STREAM_PRINCIPAL_MISMATCH"
    status_code = 403


class InvalidLastEventId(StreamError):
    code = "STREAM_INVALID_LAST_EVENT_ID"
    status_code = 400


class EventCursorExpired(StreamError):
    """The requested last id fell before the bounded tail this channel still holds."""

    code = "STREAM_CURSOR_EXPIRED"
    status_code = 410
    retryable = True


@dataclass(frozen=True)
class StreamEvent:
    """One envelope on the wire, derived from a recorded trace event.

    The id is the trace sequence as text, so a reconnecting client can send it back verbatim
    in ``Last-Event-ID`` and receive strictly later events.
    """

    id: str
    session_id: str
    sequence: int
    schema: str
    kind: str
    at: str
    fields: Mapping[str, Any]
    prev: str
    hash: str

    @classmethod
    def from_trace(cls, event: TraceEvent) -> StreamEvent:
        record = event.record()
        return cls(
            id=str(event.sequence),
            session_id=event.session_id,
            sequence=event.sequence,
            schema=str(record["schema"]),
            kind=str(event.kind),
            at=str(record["at"]),
            fields=dict(record["fields"]),
            prev=event.previous_hash,
            hash=event.digest,
        )

    def envelope(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "sequence": self.sequence,
            "schema": self.schema,
            "kind": self.kind,
            "at": self.at,
            "fields": dict(self.fields),
            "prev": self.prev,
            "hash": self.hash,
        }

    def sse(self) -> str:
        """One Server-Sent Event frame. The event name lets agents filter by trace kind."""
        data = json.dumps(self.envelope(), sort_keys=True, separators=(",", ":"))
        return f"id: {self.id}\nevent: {self.kind}\ndata: {data}\n\n"

    @staticmethod
    def parse_last_id(value: str | None) -> int | None:
        if value is None or value == "":
            return None
        try:
            parsed = int(value)
        except ValueError:
            raise InvalidLastEventId(f"{value!r} is not an event id") from None
        if parsed < 0:
            raise InvalidLastEventId(f"{value!r} is not an event id")
        return parsed


@dataclass(frozen=True)
class Lagged:
    """A typed end-of-stream marker for a subscriber that could not keep up.

    It is a control frame, not a trace event: the client is told to reconnect from its last
    event id rather than being handed a silently incomplete stream.
    """

    session_id: str

    def detail(self) -> dict[str, Any]:
        return {
            "code": "STREAM_LAGGED",
            "message": (
                f"subscriber lagged on session {self.session_id}; reconnect with the last event id"
            ),
            "retryable": True,
        }

    def envelope(self) -> dict[str, Any]:
        return {"kind": "lagged", "session_id": self.session_id, **self.detail()}

    def sse(self) -> str:
        data = json.dumps(self.envelope(), sort_keys=True, separators=(",", ":"))
        return f"event: lagged\ndata: {data}\n\n"


def principal_owner(principal: Principal) -> str:
    """The identity a channel is bound to: the authenticated subject, not a key id.

    A subject survives key rotation, so a client that reissues its key can rejoin its own
    session; a different subject cannot. The session's required scope is checked separately.
    """
    return principal.subject


Subscriber = asyncio.Queue[StreamEvent | Lagged | None]


@dataclass
class Channel:
    """The bounded tail of one session's trace and the clients following it."""

    session_id: str
    owner: str
    required_scope: str
    events: deque[StreamEvent] = field(default_factory=lambda: deque(maxlen=MAX_CHANNEL_EVENTS))
    subscribers: set[Subscriber] = field(default_factory=set)
    closed: bool = False

    def publish(self, event: TraceEvent) -> StreamEvent:
        streamed = StreamEvent.from_trace(event)
        self.events.append(streamed)
        # A queue push never awaits a client, so a slow or vanished subscriber cannot hold
        # up the run that is appending events. One that has stopped reading is closed with a
        # typed marker instead of being left to fill memory without bound.
        for queue in tuple(self.subscribers):
            try:
                queue.put_nowait(streamed)
            except asyncio.QueueFull:
                self.subscribers.discard(queue)
                self._fail_lagged(queue)
        return streamed

    def _fail_lagged(self, queue: Subscriber) -> None:
        """Replace a stalled subscriber's buffered tail with one reconnect instruction."""
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        try:
            queue.put_nowait(Lagged(self.session_id))
        except asyncio.QueueFull:
            pass

    def subscribe(self, after: int | None) -> tuple[list[StreamEvent], Subscriber]:
        oldest = self.events[0].sequence if self.events else None
        if after is not None and oldest is not None and after < oldest - 1:
            raise EventCursorExpired(
                f"event {after} has left the bounded tail of session {self.session_id}"
            )
        # Resume by sequence, not by list position: a trace need not have been recorded from
        # zero, and a truncated deque no longer starts at its first event.
        snapshot = [event for event in self.events if after is None or event.sequence > after]
        queue: Subscriber = asyncio.Queue(maxsize=MAX_SUBSCRIBER_QUEUE)
        self.subscribers.add(queue)
        if self.closed:
            queue.put_nowait(None)
        return snapshot, queue

    def unsubscribe(self, queue: Subscriber) -> None:
        self.subscribers.discard(queue)

    def close(self) -> None:
        self.closed = True
        for queue in tuple(self.subscribers):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass


def _recorded(store: TraceSink, session_id: str) -> tuple[TraceEvent, ...]:
    """The events a store already holds for a session, or none if it holds none."""
    reader = getattr(store, "read", None)
    if reader is None:
        return ()
    try:
        events = reader(session_id)
    except TraceStoreError:
        return ()
    # ``read`` is not part of the TraceSink protocol, so its result is untyped here. A store
    # that returns anything other than a sequence of recorded events is treated as holding
    # none, rather than trusting the untyped value to satisfy the declared return type.
    if not isinstance(events, (list, tuple)):
        return ()
    recorded: list[TraceEvent] = []
    for event in events:
        if not isinstance(event, TraceEvent):
            return ()
        recorded.append(event)
    return tuple(recorded)


class StreamHub:
    """The process-wide registry of session channels.

    The channel's owner and required scope are set when the session is registered by the
    producer, never by a client: a client can only present its key and ask for the tail.
    Channels are capped and evicted least-recently-used, so a closed channel stays replayable
    until it falls out of that cap rather than living for the process lifetime.
    """

    def __init__(self) -> None:
        self._channels: OrderedDict[str, Channel] = OrderedDict()
        self._tails: dict[str, TraceTail] = {}

    def _touch(self, session_id: str) -> None:
        if session_id in self._channels:
            self._channels.move_to_end(session_id)

    def _evict(self) -> None:
        while len(self._channels) > MAX_CHANNELS:
            session_id, _ = self._channels.popitem(last=False)
            self._tails.pop(session_id, None)

    def open(
        self,
        session_id: str,
        *,
        owner: Principal,
        required_scope: str,
        events: Iterable[TraceEvent] = (),
    ) -> Channel:
        identity = principal_owner(owner)
        existing = self._channels.get(session_id)
        if existing is not None:
            if existing.owner != identity or existing.required_scope != required_scope:
                raise StreamError(
                    f"session {session_id} is already open for another owner or scope"
                )
            self._touch(session_id)
            return existing
        channel = Channel(session_id=session_id, owner=identity, required_scope=required_scope)
        for event in events:
            channel.publish(event)
        self._channels[session_id] = channel
        self._evict()
        return channel

    def trace_sink(
        self,
        session_id: str,
        *,
        owner: Principal,
        required_scope: str,
        store: TraceSink | None = None,
    ) -> TraceTail:
        """The producer's seam: hand the returned sink to the loop that runs this session.

        Opening the channel here, before the loop appends anything, and mirroring every later
        append through the tail, is what makes the stream a live view of the real trace. A
        store carrying an earlier part of the session seeds the channel, so a resumed run is
        replayable from its first recorded event.
        """
        existing = self._tails.get(session_id)
        if existing is not None:
            return existing
        if store is None:
            store = MemoryTraceLog()
        self.open(
            session_id,
            owner=owner,
            required_scope=required_scope,
            events=_recorded(store, session_id),
        )
        tail = TraceTail(store)
        tail.listen(self._produce(session_id))
        self._tails[session_id] = tail
        return tail

    def _produce(self, session_id: str) -> Callable[[TraceEvent], None]:
        """Publish one recorded event and end the live stream at the trace's outcome."""

        def forward(event: TraceEvent) -> None:
            self.publish(session_id, event)
            if event.kind == TraceEventKind.OUTCOME:
                try:
                    self.close(session_id)
                except UnknownSession:
                    pass

        return forward

    def publish(self, session_id: str, event: TraceEvent) -> StreamEvent:
        channel = self._channels.get(session_id)
        if channel is None:
            raise UnknownSession(f"no stream is open for session {session_id}")
        return channel.publish(event)

    def subscribe(
        self,
        session_id: str,
        principal: Principal,
        last_event_id: str | None = None,
    ) -> tuple[list[StreamEvent], Subscriber]:
        channel = self._channels.get(session_id)
        if channel is None:
            raise UnknownSession(f"no stream is open for session {session_id}")
        if principal_owner(principal) != channel.owner:
            raise PrincipalMismatch(f"session {session_id} belongs to another principal")
        if not principal.has_scope(channel.required_scope):
            raise ScopeDenied(
                f"the {channel.required_scope!r} scope is required to stream session {session_id}"
            )
        result = channel.subscribe(StreamEvent.parse_last_id(last_event_id))
        self._touch(session_id)
        return result

    def unsubscribe(self, session_id: str, queue: Subscriber) -> None:
        channel = self._channels.get(session_id)
        if channel is not None:
            channel.unsubscribe(queue)

    def close(self, session_id: str) -> None:
        channel = self._channels.get(session_id)
        if channel is None:
            raise UnknownSession(f"no stream is open for session {session_id}")
        channel.close()

    def sessions(self) -> tuple[str, ...]:
        return tuple(sorted(self._channels))


__all__ = [
    "Channel",
    "EventCursorExpired",
    "InvalidLastEventId",
    "Lagged",
    "MAX_CHANNEL_EVENTS",
    "MAX_CHANNELS",
    "MAX_SUBSCRIBER_QUEUE",
    "PrincipalMismatch",
    "ScopeDenied",
    "StreamError",
    "StreamEvent",
    "StreamHub",
    "STREAM_PREFIX",
    "Subscriber",
    "UnknownSession",
    "principal_owner",
]
