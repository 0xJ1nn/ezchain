"""Reading a session back out of its own events: verification, anchoring and replay.

:func:`replay` rebuilds a :class:`SessionSummary` from the events alone; :func:`summarize` builds
the same summary from the live ``SessionOutcome``, and the two are equal for every recording
because turns, tool calls, spend and the terminal verdict all come from the events.

Completeness is ``replay``'s job and not ``verify_events``': a prefix of a real trace verifies
clean, so replay is what refuses one that stops before its terminal outcome.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from ..session import SessionState, StopReason
from .codec import TraceIncomplete
from .events import (
    TraceAnchor,
    TraceEvent,
    TraceEventKind,
    verify_events,
)
from .redaction import redact

if TYPE_CHECKING:
    from ..loop import SessionOutcome


@dataclass(frozen=True)
class SessionSummary:
    """What a session did, reconstructed from its trace or read off the live outcome.

    ``tool_calls`` counts the calls that produced a tool result the model was shown (status
    ``ok`` or ``error``), which is what ``SessionOutcome.observations`` holds. A call the runtime
    refused before the server saw it, and one whose transport failed, appear in the trace with
    their own status and no observation — the summary counts what the session learned, the trace
    keeps everything it attempted.
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


def _verdict(outcome: TraceEvent) -> tuple[SessionState, StopReason, str | None]:
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
    return state, reason, detail


def _spend(events: Iterable[TraceEvent]) -> tuple[int, int, Decimal, int, int]:
    turns = 0
    tokens = 0
    cost = Decimal(0)
    calls = 0
    errors = 0
    for event in events:
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
    return turns, tokens, cost, calls, errors


def replay(
    events: Iterable[TraceEvent],
    *,
    key: bytes | None = None,
    anchor: TraceAnchor | None = None,
) -> SessionSummary:
    """Rebuild the session summary from the trace alone, after verifying the chain.

    The events must be a whole session, so a prefix that ends before its terminal outcome raises
    :class:`TraceIncomplete`, and an ``anchor`` catches a truncation that cuts before an outcome
    was ever written.
    """
    ordered = tuple(events)
    verify_events(ordered, key=key, anchor=anchor)
    if not ordered:
        raise TraceIncomplete("the trace has no events")
    outcomes = [i for i, event in enumerate(ordered) if event.kind is TraceEventKind.OUTCOME]
    if not outcomes:
        raise TraceIncomplete("the trace has no recorded outcome")
    if len(outcomes) > 1:
        raise TraceIncomplete("the trace has more than one recorded outcome")
    if outcomes[0] != len(ordered) - 1:
        raise TraceIncomplete("events were recorded after the outcome")

    outcome = ordered[outcomes[0]]
    state, reason, detail = _verdict(outcome)
    turns, tokens, cost, calls, errors = _spend(ordered)
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


__all__ = ["SessionSummary", "replay", "summarize", "verify_events"]
