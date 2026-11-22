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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ezchain_agent_runtime import TraceEvent

from .api_keys import Principal

STREAM_PREFIX = "/api/v1/stream"


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


def principal_owner(principal: Principal) -> str:
    """The identity a channel is bound to: the authenticated subject, not a key id.

    A subject survives key rotation, so a client that reissues its key can rejoin its own
    session; a different subject cannot. The session's required scope is checked separately.
    """
    return principal.subject


Subscriber = asyncio.Queue[StreamEvent | None]


@dataclass
class Channel:
    """The tail of one session's trace and the clients following it."""

    session_id: str
    owner: str
    required_scope: str
    events: list[StreamEvent] = field(default_factory=list)
    subscribers: set[Subscriber] = field(default_factory=set)
    closed: bool = False

    def publish(self, event: TraceEvent) -> StreamEvent:
        streamed = StreamEvent.from_trace(event)
        self.events.append(streamed)
        # A queue push never awaits a client, so a slow or vanished subscriber cannot hold
        # up the run that is appending events.
        for queue in tuple(self.subscribers):
            queue.put_nowait(streamed)
        return streamed

    def subscribe(self, after: int | None) -> tuple[list[StreamEvent], Subscriber]:
        start = 0 if after is None else after + 1
        snapshot = list(self.events[start:])
        queue: Subscriber = asyncio.Queue()
        self.subscribers.add(queue)
        if self.closed:
            queue.put_nowait(None)
        return snapshot, queue

    def unsubscribe(self, queue: Subscriber) -> None:
        self.subscribers.discard(queue)

    def close(self) -> None:
        self.closed = True
        for queue in tuple(self.subscribers):
            queue.put_nowait(None)


class StreamHub:
    """The process-wide registry of session channels.

    The channel's owner and required scope are set when the session is registered by the
    producer, never by a client: a client can only present its key and ask for the tail.
    """

    def __init__(self) -> None:
        self._channels: dict[str, Channel] = {}

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
            return existing
        channel = Channel(session_id=session_id, owner=identity, required_scope=required_scope)
        for event in events:
            channel.publish(event)
        self._channels[session_id] = channel
        return channel

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
        return channel.subscribe(StreamEvent.parse_last_id(last_event_id))

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
    "InvalidLastEventId",
    "PrincipalMismatch",
    "ScopeDenied",
    "StreamError",
    "StreamEvent",
    "StreamHub",
    "STREAM_PREFIX",
    "UnknownSession",
    "principal_owner",
]
