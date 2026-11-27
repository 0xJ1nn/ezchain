"""A fan-out over the append-only trace; the store remains the one writer.

A tail wraps a :class:`~.events.TraceSink` and mirrors each recorded event to listeners after the
store has accepted it. It is the seam a transport attaches to: the loop still writes one chain
to one sink, and an observer receives the very event that was appended, in that order. A listener
is an observer, so one that fails is set aside rather than allowed to stop the run or change what
the store holds.
"""

from __future__ import annotations

from collections.abc import Callable

from .events import TraceEvent, TraceSink

Listener = Callable[[TraceEvent], None]


class TraceTail:
    """A :class:`~.events.TraceSink` decorator that forwards accepted events to listeners."""

    def __init__(self, sink: TraceSink) -> None:
        self._sink = sink
        self._listeners: list[Listener] = []

    @property
    def key(self) -> bytes | None:
        return self._sink.key

    @property
    def sink(self) -> TraceSink:
        """The wrapped store, so a producer can read the trace it already holds."""
        return self._sink

    def append(self, event: TraceEvent) -> None:
        # The store decides first: an event it refused is not a recorded event and must not
        # reach a listener. Only the accepted event, unmodified, is fanned out.
        self._sink.append(event)
        for listener in tuple(self._listeners):
            try:
                listener(event)
            except Exception:
                # A watcher cannot abort the run it is watching.
                continue

    def listen(self, listener: Listener) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def unlisten(self, listener: Listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)


__all__ = ["Listener", "TraceTail"]
