"""One model loop over the platform's MCP tools, inside hard budgets.

The loop owns no capability of its own. At the start of a session it reads the tool list
from the MCP server and hands exactly that list to the model; every call is checked against
the same list. There is no shell, URL, filesystem or subprocess tool anywhere in this
module — the only invoker is :meth:`McpToolbox.call_tool`.

Money numbers never come from the model: a final answer is a set of claims that
:func:`resolve_answer` checks against this session's recorded tool results, so the stated
equity cannot differ from the account read model.
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


@dataclass(frozen=True)
class RuntimeConfig:
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    session_id_factory: Callable[[], str] = _new_session_id


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
        kill_switch: KillSwitch | None = None,
        clock: Clock = utcnow,
        config: RuntimeConfig | None = None,
    ) -> None:
        self._model = model
        self._toolbox = toolbox
        self._limits = limits
        self._clock = clock
        self._daily = daily if daily is not None else DailyLedger(limits, clock=clock)
        self._kill_switch: KillSwitch = kill_switch or InMemoryKillSwitch()
        self._config = config or RuntimeConfig()

    def run(self, prompt: str) -> SessionOutcome:
        session = Session(self._config.session_id_factory())
        budget = SessionBudget(self._limits, self._daily, clock=self._clock)
        observations: list[Observation] = []

        try:
            surface = ToolSurface.from_toolbox(self._toolbox)
        except Exception as exc:
            session.fire(SessionEvent.FAIL)
            return self._outcome(
                session, StopReason.MCP_UNREACHABLE, str(exc), budget, observations
            )

        session.fire(SessionEvent.START)
        messages: list[Message] = [
            Message(role="system", content=self._config.system_prompt),
            Message(role="user", content=prompt),
        ]

        while True:
            stopped = self._gate(session, budget, observations)
            if stopped is not None:
                return stopped

            session.fire(SessionEvent.TURN)
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
                )
            budget.consume(
                turns=1,
                tokens=turn.usage.total_tokens,
                cost_usd=turn.usage.cost_usd,
            )

            if turn.tool_calls and turn.answer is not None:
                session.fire(SessionEvent.FAIL)
                return self._outcome(
                    session,
                    StopReason.MODEL_FAILED,
                    "the model returned tool calls and a final answer in one turn",
                    budget,
                    observations,
                )

            if turn.tool_calls:
                messages.append(
                    Message(role="assistant", content=turn.text, tool_calls=turn.tool_calls)
                )
                for call in turn.tool_calls:
                    stopped = self._gate(session, budget, observations)
                    if stopped is not None:
                        return stopped
                    try:
                        surface.require(call.name)
                    except ToolOutOfScope as exc:
                        session.fire(SessionEvent.FAIL)
                        return self._outcome(
                            session, StopReason.TOOL_OUT_OF_SCOPE, str(exc), budget, observations
                        )
                    session.fire(SessionEvent.TOOL_CALL)
                    try:
                        result = self._toolbox.call_tool(call.name, call.arguments)
                    except Exception as exc:
                        session.fire(SessionEvent.FAIL)
                        return self._outcome(
                            session,
                            StopReason.MCP_UNREACHABLE,
                            f"{call.name} failed: {exc}",
                            budget,
                            observations,
                        )
                    observation = self._observe(call.name, call.arguments, result)
                    observations.append(observation)
                    messages.append(
                        Message(
                            role="tool",
                            content=_json(result.data),
                            tool_call_id=call.id,
                        )
                    )
                    refusal = _refusal_code(result.data) if result.is_error else None
                    if refusal in {"KILL_SWITCH_ACTIVE", "KILL_SWITCH_ARMED"}:
                        session.fire(SessionEvent.STOP)
                        return self._outcome(
                            session,
                            StopReason.KILL_SWITCH_ACTIVE,
                            f"{call.name} was refused while the platform kill switch is armed",
                            budget,
                            observations,
                        )
                    if _is_pending_approval(result.data):
                        session.fire(SessionEvent.APPROVAL_REQUIRED)
                        return self._outcome(
                            session,
                            StopReason.AWAITING_APPROVAL,
                            f"{call.name} waits for a person to approve it",
                            budget,
                            observations,
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
                )
            try:
                answer = resolve_answer(turn.answer, tuple(observations), self._clock())
            except AnswerError as exc:
                session.fire(SessionEvent.FAIL)
                return self._outcome(session, exc.reason, str(exc), budget, observations)
            session.fire(SessionEvent.FINISH)
            return self._outcome(
                session, StopReason.COMPLETED, None, budget, observations, answer=answer
            )

    def _gate(
        self,
        session: Session,
        budget: SessionBudget,
        observations: list[Observation],
    ) -> SessionOutcome | None:
        if self._kill_switch.armed:
            session.fire(SessionEvent.STOP)
            detail = self._kill_switch.reason or "the runtime kill switch is armed"
            return self._outcome(
                session, StopReason.KILL_SWITCH_ARMED, detail, budget, observations
            )
        try:
            budget.guard()
        except BudgetExhausted as exc:
            session.fire(SessionEvent.STOP)
            return self._outcome(session, exc.reason, str(exc), budget, observations)
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
        *,
        answer: SourcedAnswer | None = None,
    ) -> SessionOutcome:
        return SessionOutcome(
            session_id=session.id,
            state=session.state,
            reason=reason,
            detail=detail,
            answer=answer,
            observations=tuple(observations),
            usage=budget.used,
            daily_usage=budget.daily_used,
        )


__all__ = [
    "AgentLoop",
    "DEFAULT_SYSTEM_PROMPT",
    "RuntimeConfig",
    "SessionOutcome",
]
