"""One model loop over the platform's MCP tools, inside hard budgets.

The loop owns no capability of its own. At the start of a session it reads the tool list
from the MCP server and hands exactly that list to the model; every call is checked against
the same list. There is no shell, URL, filesystem or subprocess tool anywhere in this
module — the only invoker is :meth:`McpToolbox.call_tool`.

Money numbers never come from the model: a final answer is a set of claims that
:func:`resolve_answer` checks against this session's recorded tool results, so the stated
equity cannot differ from the account read model.

Every session writes one append-only trace through the injected
:class:`~.trace.TraceSink`: each turn, each tool call, each budget debit, each kill-switch
observation and the terminal outcome. A trace that cannot be written stops the session —
an unaudited run is not one this runtime is allowed to make.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .answers import (
    Answer,
    AnswerError,
    Observation,
    SourcedAnswer,
    resolve_answer,
)
from .budget import (
    BudgetExhausted,
    BudgetLimits,
    Clock,
    DailyLedger,
    SessionBudget,
    Usage,
    utcnow,
)
from .killswitch import InMemoryKillSwitch, KillSwitch
from .mcp import McpToolbox, ToolOutOfScope, ToolResult, ToolSurface
from .model import Message, ModelClient, ModelRequest
from .session import Session, SessionEvent, SessionState, StopReason
from .trace import MemoryTraceLog, TraceRecorder, TraceSink

DEFAULT_SYSTEM_PROMPT = (
    "You are ezCHAIN's agent. You may call only the tools provided; they are the platform's "
    "MCP tools for this key. Never claim to have run a shell command, fetched a URL or read "
    "a file — no such tool exists. Do not state any number from memory, estimate or "
    "arithmetic: every number in a final answer must be a claim naming the tool and the "
    "dotted path into its result. The runtime checks each claim against the tool result and "
    "prints its source and age. If a tool refuses an action or an order waits for a person, "
    "say so and stop."
)


def _new_session_id() -> str:
    return uuid4().hex


# ADR-0042 forbids this service from importing ezchain_core, so the platform's refusal codes
# travel as plain strings (the format the MCP result already carries) and the two the loop
# acts on live in this local table. A test pins them against the platform's own list.
KILL_SWITCH_CODES: frozenset[str] = frozenset({"KILL_SWITCH_ACTIVE", "KILL_SWITCH_ARMED"})


def _json(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"))


def _is_pending_approval(data: Any) -> bool:
    if not isinstance(data, Mapping):
        return False
    return data.get("status") == "pending_approval" or data.get("approval_status") == "pending"


def _refusal_code(data: Any) -> str | None:
    if not isinstance(data, Mapping):
        return None
    code = data.get("code")
    return code if isinstance(code, str) else None


def _approval_id(data: Any) -> str | None:
    if not isinstance(data, Mapping):
        return None
    value = data.get("approval_id")
    return value if isinstance(value, str) else None


@dataclass(frozen=True)
class RuntimeConfig:
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    session_id_factory: Callable[[], str] = _new_session_id
    model_id: str = "unknown"


@dataclass(frozen=True)
class SessionOutcome:
    session_id: str
    state: SessionState
    reason: StopReason
    detail: str | None
    answer: SourcedAnswer | None
    observations: tuple[Observation, ...]
    usage: Usage
    daily_usage: Usage


class AgentLoop:
    def __init__(
        self,
        *,
        model: ModelClient,
        toolbox: McpToolbox,
        limits: BudgetLimits,
        daily: DailyLedger | None = None,
        agent: str | None = None,
        kill_switch: KillSwitch | None = None,
        clock: Clock = utcnow,
        config: RuntimeConfig | None = None,
        trace: TraceSink | None = None,
    ) -> None:
        self._model = model
        self._toolbox = toolbox
        self._limits = limits
        self._clock = clock
        # Every session is traced: with no sink injected the trace lives in memory for the
        # life of the loop, and a durable run hands in a FileTraceLog.
        self._trace: TraceSink = trace or MemoryTraceLog()
        # The day's spend is process-scoped and keyed by agent, never owned by the loop: a
        # loop is built per session, so a loop-local ledger would reset the day's budget on
        # every session.
        if daily is None:
            self._daily = DailyLedger(agent=agent or "default", clock=clock)
        elif agent is not None and daily.agent != agent:
            raise ValueError(f"daily ledger is for {daily.agent!r}, not {agent!r}")
        else:
            self._daily = daily
        self._kill_switch: KillSwitch = kill_switch or InMemoryKillSwitch()
        self._config = config or RuntimeConfig()

    def run(self, prompt: str) -> SessionOutcome:
        session = Session(self._config.session_id_factory())
        budget = SessionBudget(self._limits, self._daily, clock=self._clock)
        observations: list[Observation] = []
        recorder = TraceRecorder(
            session_id=session.id,
            sink=self._trace,
            clock=self._clock,
            model_id=self._config.model_id,
        )

        recorder.session_start(prompt=prompt)

        try:
            surface = ToolSurface.from_toolbox(self._toolbox)
        except Exception as exc:
            session.fire(SessionEvent.FAIL)
            return self._outcome(
                session, StopReason.MCP_UNREACHABLE, str(exc), budget, observations, recorder
            )

        session.fire(SessionEvent.START)
        messages: list[Message] = [
            Message(role="system", content=self._config.system_prompt),
            Message(role="user", content=prompt),
        ]

        turn_index = 0
        while True:
            stopped = self._gate(session, budget, observations, recorder)
            if stopped is not None:
                return stopped

            session.fire(SessionEvent.TURN)
            elapsed = recorder.elapsed()
            try:
                turn = self._model.complete(
                    ModelRequest(messages=tuple(messages), tools=surface.model_tools())
                )
            except Exception as exc:
                session.fire(SessionEvent.FAIL)
                return self._outcome(
                    session,
                    StopReason.MODEL_FAILED,
                    f"model call failed: {exc}",
                    budget,
                    observations,
                    recorder,
                )
            recorder.turn(
                index=turn_index,
                prompt_tokens=turn.usage.prompt_tokens,
                completion_tokens=turn.usage.completion_tokens,
                cost_usd=turn.usage.cost_usd,
                tool_calls=len(turn.tool_calls),
                has_answer=turn.answer is not None,
                latency_ms=elapsed(),
            )
            turn_index += 1
            budget.consume(
                turns=1,
                tokens=turn.usage.total_tokens,
                cost_usd=turn.usage.cost_usd,
            )
            recorder.budget_debit(
                turns=1,
                tokens=turn.usage.total_tokens,
                cost_usd=turn.usage.cost_usd,
                session=budget.used,
                daily=budget.daily_used,
            )
            # The gate above ran before this turn was paid for, so a turn that crosses a
            # limit is only visible here. Refusing now keeps a budget overrun from
            # producing an answer: a session that crossed a limit never reports COMPLETED.
            overrun = budget.overtaken()
            if overrun is not None:
                session.fire(SessionEvent.STOP)
                return self._outcome(
                    session, overrun.reason, str(overrun), budget, observations, recorder
                )

            if turn.tool_calls and turn.answer is not None:
                session.fire(SessionEvent.FAIL)
                return self._outcome(
                    session,
                    StopReason.MODEL_FAILED,
                    "the model returned tool calls and a final answer in one turn",
                    budget,
                    observations,
                    recorder,
                )

            if turn.tool_calls:
                messages.append(
                    Message(role="assistant", content=turn.text, tool_calls=turn.tool_calls)
                )
                for call in turn.tool_calls:
                    stopped = self._gate(session, budget, observations, recorder)
                    if stopped is not None:
                        return stopped
                    try:
                        surface.require(call.name)
                    except ToolOutOfScope as exc:
                        recorder.tool_call(
                            call_id=call.id,
                            tool=call.name,
                            arguments=call.arguments,
                            status="refused",
                            code=StopReason.TOOL_OUT_OF_SCOPE.value,
                        )
                        session.fire(SessionEvent.FAIL)
                        return self._outcome(
                            session,
                            StopReason.TOOL_OUT_OF_SCOPE,
                            str(exc),
                            budget,
                            observations,
                            recorder,
                        )
                    session.fire(SessionEvent.TOOL_CALL)
                    elapsed = recorder.elapsed()
                    try:
                        result = self._toolbox.call_tool(call.name, call.arguments)
                    except Exception as exc:
                        recorder.tool_call(
                            call_id=call.id,
                            tool=call.name,
                            arguments=call.arguments,
                            status="failed",
                            code=StopReason.MCP_UNREACHABLE.value,
                            latency_ms=elapsed(),
                        )
                        session.fire(SessionEvent.FAIL)
                        return self._outcome(
                            session,
                            StopReason.MCP_UNREACHABLE,
                            f"{call.name} failed: {exc}",
                            budget,
                            observations,
                            recorder,
                        )
                    observation = self._observe(call.name, call.arguments, result)
                    observations.append(observation)
                    refusal = _refusal_code(result.data) if result.is_error else None
                    recorder.tool_call(
                        call_id=call.id,
                        tool=call.name,
                        arguments=call.arguments,
                        status="error" if result.is_error else "ok",
                        code=refusal,
                        result=result.data,
                        latency_ms=elapsed(),
                    )
                    messages.append(
                        Message(
                            role="tool",
                            content=_json(result.data),
                            tool_call_id=call.id,
                        )
                    )
                    if refusal in KILL_SWITCH_CODES:
                        session.fire(SessionEvent.STOP)
                        return self._outcome(
                            session,
                            StopReason.KILL_SWITCH_ACTIVE,
                            f"{call.name} was refused while the platform kill switch is armed",
                            budget,
                            observations,
                            recorder,
                        )
                    if _is_pending_approval(result.data):
                        session.fire(SessionEvent.APPROVAL_REQUIRED)
                        return self._outcome(
                            session,
                            StopReason.AWAITING_APPROVAL,
                            f"{call.name} waits for a person to approve it",
                            budget,
                            observations,
                            recorder,
                            approval_id=_approval_id(result.data),
                        )
                continue

            if not isinstance(turn.answer, Answer):
                session.fire(SessionEvent.FAIL)
                return self._outcome(
                    session,
                    StopReason.MODEL_FAILED,
                    "the model returned neither tool calls nor a final answer",
                    budget,
                    observations,
                    recorder,
                )
            try:
                answer = resolve_answer(turn.answer, tuple(observations), self._clock())
            except AnswerError as exc:
                session.fire(SessionEvent.FAIL)
                return self._outcome(session, exc.reason, str(exc), budget, observations, recorder)
            session.fire(SessionEvent.FINISH)
            return self._outcome(
                session, StopReason.COMPLETED, None, budget, observations, recorder, answer=answer
            )

    def _gate(
        self,
        session: Session,
        budget: SessionBudget,
        observations: list[Observation],
        recorder: TraceRecorder,
    ) -> SessionOutcome | None:
        armed = self._kill_switch.armed
        recorder.kill_switch(armed=armed, reason=self._kill_switch.reason)
        if armed:
            session.fire(SessionEvent.STOP)
            detail = self._kill_switch.reason or "the runtime kill switch is armed"
            return self._outcome(
                session, StopReason.KILL_SWITCH_ARMED, detail, budget, observations, recorder
            )
        try:
            budget.guard()
        except BudgetExhausted as exc:
            session.fire(SessionEvent.STOP)
            return self._outcome(session, exc.reason, str(exc), budget, observations, recorder)
        return None

    def _observe(
        self,
        tool: str,
        arguments: Mapping[str, Any],
        result: ToolResult,
    ) -> Observation:
        return Observation(
            tool=tool,
            arguments=dict(arguments),
            data=result.data,
            observed_at=self._clock(),
            is_error=result.is_error,
        )

    def _outcome(
        self,
        session: Session,
        reason: StopReason,
        detail: str | None,
        budget: SessionBudget,
        observations: list[Observation],
        recorder: TraceRecorder,
        *,
        answer: SourcedAnswer | None = None,
        approval_id: str | None = None,
    ) -> SessionOutcome:
        usage = budget.used
        numbers = 0 if answer is None else len(answer.numbers)
        recorder.outcome(
            state=session.state,
            reason=reason,
            detail=detail,
            turns=usage.turns,
            tokens=usage.tokens,
            cost_usd=usage.cost_usd,
            numbers=numbers,
            approval_id=approval_id,
        )
        return SessionOutcome(
            session_id=session.id,
            state=session.state,
            reason=reason,
            detail=detail,
            answer=answer,
            observations=tuple(observations),
            usage=usage,
            daily_usage=budget.daily_used,
        )


__all__ = [
    "AgentLoop",
    "DEFAULT_SYSTEM_PROMPT",
    "RuntimeConfig",
    "SessionOutcome",
]
