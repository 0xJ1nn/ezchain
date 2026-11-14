"""The session trace: append-only, hash-chained and replayable.

ADR-0042 names the runtime's audit trail: one append-only trace per session, in the runtime's
own store, never in the platform database, redacted on write, replayable against the recorded
results. This module is that store.

**Append only.** :class:`TraceSink` has one operation, ``append``. The stores add ``read``,
``sessions`` and (for the file store) ``path_for``, and nothing else: there is no update, no
delete and no rewrite path anywhere in the API, so append-only is a property of the interface
rather than a convention someone has to remember.

**Tamper evidence.** Every event carries the digest of the event before it, and an event's
digest is the SHA-256 of its canonical encoding. The encoding is one JSON object with sorted
keys over a validated type domain — never a delimiter join, because the ledger seal review
found that a ``"|"`` join let two different tapes hash the same. :func:`canonical`
is injective over the domain it accepts: ``1`` and ``"1"`` encode differently, a float or a
``Decimal`` is refused rather than guessed at, and a field containing ``"|"`` cannot move a
boundary because there are no boundaries to move.

**Replay.** :func:`replay` rebuilds a :class:`SessionSummary` from the events alone;
:func:`summarize` builds the same summary from the live ``SessionOutcome``. The two are equal
for every recording, because turns, tool calls, spend and the terminal verdict all come from
the events.

**Redaction.** :meth:`TraceEvent.create` redacts credential-shaped strings in every field, so
a token that reaches a detail message or a tool name is stored as ``[redacted]``. Tool
arguments are stored as a digest, never as their text.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from .budget import Clock, Usage
from .session import SessionState, StopReason

if TYPE_CHECKING:
    from .loop import SessionOutcome

TRACE_SCHEMA = "ezchain.agent.trace.v1"
GENESIS = "0" * 64
REDACTED = "[redacted]"
_RECORD_KEYS = frozenset({"schema", "session_id", "seq", "kind", "at", "fields", "prev", "hash"})

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bezk_[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
)

_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class TraceError(RuntimeError):
    """Base for every trace failure."""


class TraceEncodingError(TraceError):
    """A value has no canonical form, so hashing it would be a guess."""


class TraceStoreError(TraceError):
    """The store refused the append or could not read what it was given."""


class TraceWriteFailed(TraceError):
    """The recorder could not persist an event; the session must not proceed."""


class TraceIncomplete(TraceError):
    """The events do not describe a finished session."""


class TraceChainBroken(TraceError):
    """The chain does not verify at one event."""

    def __init__(self, index: int, reason: str, detail: str) -> None:
        super().__init__(f"trace event {index} fails verification ({reason}): {detail}")
        self.index = index
        self.reason = reason


class TraceEventKind(StrEnum):
    SESSION_START = "session_start"
    TURN = "turn"
    TOOL_CALL = "tool_call"
    BUDGET_DEBIT = "budget_debit"
    KILL_SWITCH = "kill_switch"
    OUTCOME = "outcome"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _check(value: Any, path: str) -> Any:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float | Decimal):
        raise TraceEncodingError(
            f"{path}: {type(value).__name__} has no canonical form; format it as a string first"
        )
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TraceEncodingError(f"{path}: a field name must be a string, not {key!r}")
            out[key] = _check(item, f"{path}.{key}")
        return out
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [_check(item, f"{path}[{index}]") for index, item in enumerate(value)]
    raise TraceEncodingError(f"{path}: {type(value).__name__} has no canonical form")


def canonical(record: Mapping[str, Any]) -> str:
    """One canonical encoding, so two different records cannot share a digest.

    JSON over a validated, JSON-native type domain with sorted keys and no whitespace. The
    encoding is injective on that domain: no separator exists to smuggle a field boundary
    across, a string and a number of the same text stay distinct, and anything whose encoding
    would depend on a platform detail (a float, a ``Decimal``) is refused.
    """
    return json.dumps(
        _check(record, "record"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def digest_text(text: str) -> str:
    return _sha(text)


def digest_json(value: Any) -> str:
    """A digest of arbitrary tool data, never a copy of it.

    Tool arguments and results are JSON-RPC data, so they are already JSON-native; anything
    else is refused rather than stringified, because ``str()`` would let ``Decimal("1")`` and
    the string ``"1"`` share a digest. An integer and a float encode differently (``1`` vs
    ``1.0``), and an object whose values contain a delimiter cannot collide with a sibling.
    """
    try:
        encoded = json.dumps(
            _tool_data(value, "data"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise TraceEncodingError(f"tool data has no canonical form: {exc}") from exc
    return _sha(encoded)


def _tool_data(value: Any, path: str) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TraceEncodingError(f"{path}: a JSON object key must be a string, not {key!r}")
            out[key] = _tool_data(item, f"{path}.{key}")
        return out
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [_tool_data(item, f"{path}[{index}]") for index, item in enumerate(value)]
    raise TraceEncodingError(f"{path}: {type(value).__name__} has no canonical form")


def money(value: Decimal) -> str:
    """A Decimal as the one string that means it, e.g. ``Decimal("0.020")`` → ``"0.02"``."""
    if not isinstance(value, Decimal):
        raise TraceEncodingError("money must be a Decimal")
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def redact(value: Any) -> Any:
    """Replace credential-shaped strings, recursively. Never mutates the caller's data."""
    if isinstance(value, str):
        for pattern in _SECRET_PATTERNS:
            value = pattern.sub(REDACTED, value)
        return value
    if isinstance(value, Mapping):
        return {key: redact(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [redact(item) for item in value]
    return value


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
            digest=_sha(canonical(payload)),
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
    """One operation. A sink that cannot append is not a sink."""

    def append(self, event: TraceEvent) -> None: ...


def _expect_chain(last: TraceEvent | None, event: TraceEvent) -> None:
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


class MemoryTraceLog:
    """The same contract in memory, for a runtime with no filesystem access."""

    def __init__(self) -> None:
        self._events: dict[str, list[TraceEvent]] = {}

    def append(self, event: TraceEvent) -> None:
        events = self._events.get(event.session_id, [])
        _expect_chain(events[-1] if events else None, event)
        self._events.setdefault(event.session_id, []).append(event)

    def read(self, session_id: str) -> tuple[TraceEvent, ...]:
        try:
            return tuple(self._events[session_id])
        except KeyError:
            raise TraceStoreError(f"no trace for session {session_id}") from None

    def sessions(self) -> tuple[str, ...]:
        return tuple(sorted(self._events))


class FileTraceLog:
    """One JSON lines file per session, opened append-only or read-only.

    The store reads the last line before it appends, so a second process cannot continue a
    session it did not record: a session id is one file, and one file is one chain.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def path_for(self, session_id: str) -> Path:
        if not _SAFE_SESSION_ID.match(session_id) or ".." in session_id:
            raise TraceStoreError(f"{session_id!r} is not a usable session id")
        return self._root / f"{session_id}.jsonl"

    def append(self, event: TraceEvent) -> None:
        path = self.path_for(event.session_id)
        _expect_chain(self._last(path), event)
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(event.line() + "\n")
        except OSError as exc:
            raise TraceStoreError(f"session {event.session_id}: {exc}") from exc

    def read(self, session_id: str) -> tuple[TraceEvent, ...]:
        path = self.path_for(session_id)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise TraceStoreError(f"no trace for session {session_id}: {exc}") from exc
        events = []
        for number, line in enumerate(text.splitlines(), start=1):
            if line.strip():
                events.append(self._parse(session_id, number, line))
        return tuple(events)

    def sessions(self) -> tuple[str, ...]:
        return tuple(sorted(path.stem for path in self._root.glob("*.jsonl")))

    def _last(self, path: Path) -> TraceEvent | None:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise TraceStoreError(f"{path.name}: {exc}") from exc
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            return None
        session_id = path.stem
        return self._parse(session_id, len(lines), lines[-1])

    def _parse(self, session_id: str, number: int, line: str) -> TraceEvent:
        try:
            stored = json.loads(line)
            if not isinstance(stored, Mapping):
                raise TraceStoreError(f"{session_id}: line {number} is not a trace object")
            if set(stored) != _RECORD_KEYS:
                raise TraceStoreError(
                    f"{session_id}: line {number} carries {sorted(set(stored) - _RECORD_KEYS)} "
                    f"or is missing {sorted(_RECORD_KEYS - set(stored))}"
                )
            if stored["schema"] != TRACE_SCHEMA:
                raise TraceStoreError(f"{session_id}: line {number} has an unknown schema")
            if stored.get("session_id") != session_id:
                raise TraceStoreError(f"{session_id}: line {number} belongs to another session")
            kind = TraceEventKind(stored["kind"])
            at = datetime.fromisoformat(stored["at"])
            previous_hash = stored["prev"]
            digest = stored["hash"]
            fields = stored["fields"]
            sequence = stored["seq"]
            if not isinstance(previous_hash, str) or not isinstance(digest, str):
                raise TraceStoreError(f"{session_id}: line {number} has a non-text digest")
            if isinstance(sequence, bool) or not isinstance(sequence, int):
                raise TraceStoreError(f"{session_id}: line {number} has a non-integer step")
            if not isinstance(fields, Mapping):
                raise TraceStoreError(f"{session_id}: line {number} has no field object")
        except TraceStoreError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise TraceStoreError(
                f"{session_id}: line {number} is not a trace event: {exc}"
            ) from exc
        return TraceEvent(
            session_id=session_id,
            sequence=sequence,
            kind=kind,
            at=at,
            fields=fields,
            previous_hash=previous_hash,
            digest=digest,
        )


def verify_events(events: Iterable[TraceEvent]) -> None:
    """Recompute the chain; raise :class:`TraceChainBroken` at the first event that differs."""
    head = GENESIS
    session_id: str | None = None
    for index, event in enumerate(events):
        if session_id is None:
            session_id = event.session_id
        elif event.session_id != session_id:
            raise TraceChainBroken(index, "session", f"belongs to {event.session_id}")
        if event.sequence != index:
            raise TraceChainBroken(index, "sequence", f"recorded as {event.sequence}")
        if _sha(canonical(event.record())) != event.digest:
            raise TraceChainBroken(index, "digest", "the event's own digest does not match")
        if event.previous_hash != head:
            raise TraceChainBroken(index, "previous_hash", "it does not name its predecessor")
        head = event.digest


@dataclass(frozen=True)
class SessionSummary:
    """What a session did, reconstructed from its trace or read off the live outcome.

    ``tool_calls`` counts the calls that produced a tool result the model was shown (status
    ``ok`` or ``error``), which is what ``SessionOutcome.observations`` holds. A call the
    runtime refused before the server saw it, and one whose transport failed, appear in the
    trace with their own status and no observation — the summary counts what the session
    learned, the trace keeps everything it attempted.
    """

    session_id: str
    state: SessionState
    reason: StopReason
    detail: str | None
    turns: int
    tool_calls: int
    tool_errors: int
    tokens: int
    cost_usd: Decimal
    numbers: int


def _field(event: TraceEvent, name: str) -> Any:
    try:
        return event.fields[name]
    except KeyError:
        raise TraceIncomplete(f"event {event.sequence} ({event.kind}) has no {name!r}") from None


def replay(events: Iterable[TraceEvent]) -> SessionSummary:
    """Rebuild the session summary from the trace alone, after verifying the chain."""
    ordered = tuple(events)
    verify_events(ordered)
    if not ordered:
        raise TraceIncomplete("the trace has no events")
    outcomes = [
        index for index, event in enumerate(ordered) if event.kind is TraceEventKind.OUTCOME
    ]
    if not outcomes:
        raise TraceIncomplete("the trace has no recorded outcome")
    if len(outcomes) > 1:
        raise TraceIncomplete("the trace has more than one recorded outcome")
    if outcomes[0] != len(ordered) - 1:
        raise TraceIncomplete("events were recorded after the outcome")

    outcome = ordered[outcomes[0]]
    try:
        state = SessionState(_field(outcome, "state"))
    except ValueError as exc:
        raise TraceIncomplete(f"the recorded state is not a session state: {exc}") from exc
    if state is SessionState.RUNNING or state is SessionState.IDLE:
        raise TraceIncomplete(f"the recorded outcome has no verdict: {state.value}")
    reason = StopReason(_field(outcome, "reason"))
    detail = _field(outcome, "detail")
    if detail is not None and not isinstance(detail, str):
        raise TraceIncomplete("the recorded detail is not text")

    turns = 0
    tokens = 0
    cost = Decimal(0)
    calls = 0
    errors = 0
    for event in ordered:
        if event.kind is TraceEventKind.TURN:
            turns += 1
            tokens += int(_field(event, "tokens"))
            cost += Decimal(str(_field(event, "cost_usd")))
        elif event.kind is TraceEventKind.TOOL_CALL:
            status = _field(event, "status")
            if status == "ok":
                calls += 1
            elif status == "error":
                calls += 1
                errors += 1
            elif status not in {"failed", "refused"}:
                raise TraceIncomplete(f"event {event.sequence} has an unknown status {status!r}")
    return SessionSummary(
        session_id=ordered[0].session_id,
        state=state,
        reason=reason,
        detail=detail,
        turns=turns,
        tool_calls=calls,
        tool_errors=errors,
        tokens=tokens,
        cost_usd=cost,
        numbers=int(_field(outcome, "numbers")),
    )


def summarize(outcome: SessionOutcome) -> SessionSummary:
    """The live summary replay must reproduce, built from one finished session."""
    detail = None if outcome.detail is None else str(redact(outcome.detail))
    return SessionSummary(
        session_id=outcome.session_id,
        state=outcome.state,
        reason=outcome.reason,
        detail=detail,
        turns=outcome.usage.turns,
        tool_calls=len(outcome.observations),
        tool_errors=sum(1 for observation in outcome.observations if observation.is_error),
        tokens=outcome.usage.tokens,
        cost_usd=outcome.usage.cost_usd,
        numbers=0 if outcome.answer is None else len(outcome.answer.numbers),
    )


class TraceRecorder:
    """The one writer of a session's chain: it holds the head and nothing else."""

    def __init__(
        self,
        *,
        session_id: str,
        sink: TraceSink,
        clock: Clock,
        model_id: str = "unknown",
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_id = session_id
        self._sink = sink
        self._clock = clock
        self._model_id = model_id
        self._monotonic = monotonic
        self._head = GENESIS
        self._sequence = 0

    @property
    def head(self) -> str:
        return self._head

    def elapsed(self) -> Callable[[], int]:
        """A stopwatch for one call, in whole milliseconds."""
        started = self._monotonic()

        def stop() -> int:
            return round((self._monotonic() - started) * 1000)

        return stop

    def _append(self, kind: TraceEventKind, fields: Mapping[str, Any]) -> None:
        try:
            event = TraceEvent.create(
                session_id=self._session_id,
                sequence=self._sequence,
                kind=kind,
                at=self._clock(),
                fields=fields,
                previous_hash=self._head,
            )
        except TraceEncodingError as exc:
            raise TraceWriteFailed(
                f"session {self._session_id}: the event has no canonical form: {exc}"
            ) from exc
        try:
            self._sink.append(event)
        except TraceStoreError as exc:
            raise TraceWriteFailed(
                f"session {self._session_id}: the trace could not be appended: {exc}"
            ) from exc
        self._head = event.digest
        self._sequence += 1

    def session_start(self, *, prompt: str, model_id: str | None = None) -> None:
        self._append(
            TraceEventKind.SESSION_START,
            {"prompt_hash": digest_text(prompt), "model_id": model_id or self._model_id},
        )

    def kill_switch(self, *, armed: bool, reason: str) -> None:
        self._append(TraceEventKind.KILL_SWITCH, {"armed": armed, "reason": reason})

    def turn(
        self,
        *,
        index: int,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: Decimal,
        tool_calls: int,
        has_answer: bool,
        latency_ms: int = 0,
        model_id: str | None = None,
    ) -> None:
        self._append(
            TraceEventKind.TURN,
            {
                "index": index,
                "model_id": model_id or self._model_id,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "tokens": prompt_tokens + completion_tokens,
                "cost_usd": money(cost_usd),
                "latency_ms": latency_ms,
                "tool_calls": tool_calls,
                "has_answer": has_answer,
            },
        )

    def budget_debit(
        self, *, turns: int, tokens: int, cost_usd: Decimal, session: Usage, daily: Usage
    ) -> None:
        self._append(
            TraceEventKind.BUDGET_DEBIT,
            {
                "turns": turns,
                "tokens": tokens,
                "cost_usd": money(cost_usd),
                "session": _usage(session),
                "daily": _usage(daily),
            },
        )

    def tool_call(
        self,
        *,
        call_id: str,
        tool: str,
        arguments: Mapping[str, Any],
        status: str,
        code: str | None = None,
        result: Any = None,
        latency_ms: int = 0,
    ) -> None:
        self._append(
            TraceEventKind.TOOL_CALL,
            {
                "call_id": call_id,
                "tool": tool,
                "arguments_digest": digest_json(arguments),
                "status": status,
                "code": code,
                "result_digest": None if result is None else digest_json(result),
                "latency_ms": latency_ms,
            },
        )

    def outcome(
        self,
        *,
        state: SessionState,
        reason: StopReason,
        detail: str | None,
        turns: int,
        tokens: int,
        cost_usd: Decimal,
        numbers: int,
        approval_id: str | None = None,
    ) -> None:
        self._append(
            TraceEventKind.OUTCOME,
            {
                "state": state.value,
                "reason": reason.value,
                "detail": detail,
                "turns": turns,
                "tokens": tokens,
                "cost_usd": money(cost_usd),
                "numbers": numbers,
                "approval_id": approval_id,
            },
        )


def _usage(usage: Usage) -> dict[str, Any]:
    return {"turns": usage.turns, "tokens": usage.tokens, "cost_usd": money(usage.cost_usd)}


__all__ = [
    "GENESIS",
    "REDACTED",
    "TRACE_SCHEMA",
    "FileTraceLog",
    "MemoryTraceLog",
    "SessionSummary",
    "TraceChainBroken",
    "TraceEncodingError",
    "TraceError",
    "TraceEvent",
    "TraceEventKind",
    "TraceIncomplete",
    "TraceRecorder",
    "TraceSink",
    "TraceStoreError",
    "TraceWriteFailed",
    "canonical",
    "digest_json",
    "digest_text",
    "money",
    "redact",
    "replay",
    "summarize",
    "verify_events",
]
