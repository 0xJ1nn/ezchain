"""The one writer of a session's chain: it holds the head and the sealing key.

Every field is redacted on the way in through :meth:`TraceEvent.create`, so the recorder cannot
store a credential it was handed; a value with no canonical form is refused and becomes
:class:`TraceWriteFailed` rather than an unaudited session.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any

from ..budget import Clock, Usage
from ..session import SessionState, StopReason
from .codec import (
    TraceEncodingError,
    TraceStoreError,
    TraceWriteFailed,
    digest_json,
    digest_text,
    money,
)
from .events import GENESIS, TraceEvent, TraceEventKind, TraceSink


class TraceRecorder:
    """The one writer of a session's chain: the head, the step and the key, and nothing else."""

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
        self._key = sink.key
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
                key=self._key,
            )
        except TraceEncodingError as exc:
            raise TraceWriteFailed(
                f"session {self._session_id}: the event has no canonical form: {exc}"
            ) from exc
        except ValueError as exc:
            raise TraceWriteFailed(
                f"session {self._session_id}: the event cannot be recorded: {exc}"
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


__all__ = ["TraceRecorder", "TraceSink"]
