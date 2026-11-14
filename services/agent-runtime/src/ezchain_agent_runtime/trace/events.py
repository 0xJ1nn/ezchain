"""The event, the chain and its verification.

An event carries the digest of the one before it, and its own digest is taken over its canonical
encoding (:mod:`.codec`) sealed with the store's key (:mod:`.key`). That construction is what the
module docstring of :mod:`.trace` describes, and this is where it lives.

**Integrity is not completeness.** :func:`verify_events` checks the events it is given, so an
empty sequence and any prefix of a real trace verify clean; without an anchor it cannot know how
long the trace was meant to be. Deciding a trace is a *finished* session is
:func:`~.replay.replay`'s job, and it refuses one whose prefix dangles.
"""

from __future__ import annotations

import hmac
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from .codec import (
    TraceChainBroken,
    TraceEncodingError,
    TraceIncomplete,
    TraceStoreError,
    canonical,
)
from .key import is_keyed, seal
from .redaction import redact

TRACE_SCHEMA = "ezchain.agent.trace.v1"
GENESIS = "0" * 64


class TraceEventKind(StrEnum):
    SESSION_START = "session_start"
    TURN = "turn"
    TOOL_CALL = "tool_call"
    BUDGET_DEBIT = "budget_debit"
    KILL_SWITCH = "kill_switch"
    OUTCOME = "outcome"


def _at(value: datetime) -> str:
    if value.tzinfo is None:
        raise TraceEncodingError("an event time must carry a timezone")
    return value.astimezone(UTC).isoformat()


def _payload(
    *,
    session_id: str,
    sequence: int,
    kind: TraceEventKind,
    at: datetime,
    fields: Mapping[str, Any],
    previous_hash: str,
) -> dict[str, Any]:
    return {
        "schema": TRACE_SCHEMA,
        "session_id": session_id,
        "seq": sequence,
        "kind": str(kind),
        "at": _at(at),
        "fields": dict(fields),
        "prev": previous_hash,
    }


@dataclass(frozen=True)
class TraceEvent:
    session_id: str
    sequence: int
    kind: TraceEventKind
    at: datetime
    fields: Mapping[str, Any]
    previous_hash: str
    digest: str

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        sequence: int,
        kind: TraceEventKind,
        at: datetime,
        fields: Mapping[str, Any],
        previous_hash: str,
        key: bytes | None = None,
    ) -> TraceEvent:
        payload = _payload(
            session_id=session_id,
            sequence=sequence,
            kind=kind,
            at=at,
            fields=redact(dict(fields)),
            previous_hash=previous_hash,
        )
        return cls(
            session_id=session_id,
            sequence=sequence,
            kind=kind,
            at=at,
            fields=payload["fields"],
            previous_hash=previous_hash,
            digest=seal(canonical(payload), key),
        )

    def record(self) -> dict[str, Any]:
        """The bytes that get hashed, without the digest itself."""
        return _payload(
            session_id=self.session_id,
            sequence=self.sequence,
            kind=self.kind,
            at=self.at,
            fields=self.fields,
            previous_hash=self.previous_hash,
        )

    def line(self) -> str:
        """The stored form: the record plus its digest, canonically encoded."""
        return canonical({**self.record(), "hash": self.digest})


class TraceSink(Protocol):
    """One operation, plus the key it seals with. A sink that cannot append is not a sink."""

    @property
    def key(self) -> bytes | None: ...

    def append(self, event: TraceEvent) -> None: ...


def expect_chain(last: TraceEvent | None, event: TraceEvent) -> None:
    """An event must name its predecessor and take the next step, or the store refuses it."""
    if last is None:
        if event.previous_hash != GENESIS:
            raise TraceStoreError(
                f"session {event.session_id}: the first event must start from GENESIS"
            )
        if event.sequence != 0:
            raise TraceStoreError(f"session {event.session_id}: the first event must be 0")
        return
    if event.previous_hash != last.digest:
        raise TraceStoreError(
            f"session {event.session_id}: event {event.sequence} does not extend the chain"
        )
    if event.sequence != last.sequence + 1:
        raise TraceStoreError(
            f"session {event.session_id}: expected event {last.sequence + 1}, got {event.sequence}"
        )


def expect_seal(key: bytes | None, event: TraceEvent) -> None:
    """A keyed store takes only keyed events, and an unkeyed one only unkeyed events.

    Without this an event sealed with a different or absent key could be appended to a chain and
    the mismatch would surface only at verification, after the file had claimed the step.
    """
    if is_keyed(event.digest) != (key is not None):
        wanted = "keyed" if key is not None else "unkeyed"
        raise TraceStoreError(
            f"session {event.session_id}: event {event.sequence} is not {wanted} like this store"
        )


@dataclass(frozen=True)
class TraceAnchor:
    """What a trace is expected to end with, stored away from the trace directory.

    A key stops a forger; it does not stop a truncation, because deleting events needs no
    digest. Record this after each session (or after each event, for a stricter audit) where the
    trace's writer cannot rewrite it, and hand it to :func:`verify_events` or
    :func:`~.replay.replay`.
    """

    session_id: str
    count: int
    head: str


def anchor_of(events: Iterable[TraceEvent]) -> TraceAnchor:
    """The anchor for a trace: its session, its event count and its head digest."""
    ordered = tuple(events)
    if not ordered:
        raise TraceIncomplete("an empty trace cannot be anchored")
    return TraceAnchor(
        session_id=ordered[0].session_id, count=len(ordered), head=ordered[-1].digest
    )


def verify_events(
    events: Iterable[TraceEvent],
    *,
    key: bytes | None = None,
    anchor: TraceAnchor | None = None,
) -> None:
    """Recompute the chain; raise :class:`TraceChainBroken` at the first event that differs.

    This checks the events it is given, not how many there should be: an empty sequence and any
    prefix of a real trace verify clean until the truncation is caught by ``anchor`` or by
    :func:`~.replay.replay`, which refuses a trace with no terminal outcome. A keyed trace is
    refused without its key, and an unkeyed trace is refused when a key is supplied, so
    verification cannot be made to pass by rewriting the prefix unkeyed.
    """
    head = GENESIS
    session_id: str | None = None
    count = 0
    for index, event in enumerate(events):
        if session_id is None:
            session_id = event.session_id
        elif event.session_id != session_id:
            raise TraceChainBroken(index, "session", f"belongs to {event.session_id}")
        if event.sequence != index:
            raise TraceChainBroken(index, "sequence", f"recorded as {event.sequence}")
        keyed = is_keyed(event.digest)
        if keyed and key is None:
            raise TraceChainBroken(
                index, "key", "the event is sealed with a key, and no key was supplied"
            )
        if key is not None and not keyed:
            raise TraceChainBroken(
                index, "key", "the event carries an unkeyed digest, and a key was supplied"
            )
        try:
            recomputed = seal(canonical(event.record()), key)
        except TraceEncodingError as exc:
            raise TraceChainBroken(index, "encoding", str(exc)) from exc
        if not hmac.compare_digest(recomputed, event.digest):
            raise TraceChainBroken(index, "digest", "the event's own digest does not match")
        if event.previous_hash != head:
            raise TraceChainBroken(index, "previous_hash", "it does not name its predecessor")
        head = event.digest
        count += 1
    if anchor is not None:
        _check_anchor(anchor, session_id, count, head)


def _check_anchor(anchor: TraceAnchor, session_id: str | None, count: int, head: str) -> None:
    if session_id is not None and session_id != anchor.session_id:
        raise TraceChainBroken(0, "anchor", f"the anchor records {anchor.session_id}")
    if count < anchor.count:
        raise TraceChainBroken(
            count,
            "truncated",
            f"the trace has {count} events and its anchor records {anchor.count}",
        )
    if count > anchor.count:
        raise TraceChainBroken(
            anchor.count,
            "anchor",
            f"the trace has {count} events and its anchor records {anchor.count}",
        )
    if head != anchor.head:
        raise TraceChainBroken(count - 1, "anchor", "the head does not match the anchor")


__all__ = [
    "GENESIS",
    "TRACE_SCHEMA",
    "TraceAnchor",
    "TraceChainBroken",
    "TraceEncodingError",
    "TraceEvent",
    "TraceEventKind",
    "TraceSink",
    "TraceStoreError",
    "anchor_of",
    "expect_chain",
    "expect_seal",
    "verify_events",
]
