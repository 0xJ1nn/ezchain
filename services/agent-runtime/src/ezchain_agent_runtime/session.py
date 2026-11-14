"""Session lifecycle and the typed reason a session stopped.

:data:`TRANSITIONS` is the one table. Every move a session can make is a row here, and
:class:`Session` knows no transitions of its own. The six states and the edges between them
are the contract the loop, the panel and the trace all read.
"""

from __future__ import annotations

from enum import StrEnum


class SessionState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    DONE = "done"
    STOPPED = "stopped"
    FAILED = "failed"


class SessionEvent(StrEnum):
    START = "start"
    TURN = "turn"
    TOOL_CALL = "tool_call"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_DECIDED = "approval_decided"
    FINISH = "finish"
    STOP = "stop"
    FAIL = "fail"


TERMINAL_STATES: frozenset[SessionState] = frozenset(
    {SessionState.DONE, SessionState.STOPPED, SessionState.FAILED}
)

TRANSITIONS: dict[tuple[SessionState, SessionEvent], SessionState] = {
    (SessionState.IDLE, SessionEvent.START): SessionState.RUNNING,
    (SessionState.IDLE, SessionEvent.STOP): SessionState.STOPPED,
    (SessionState.IDLE, SessionEvent.FAIL): SessionState.FAILED,
    (SessionState.RUNNING, SessionEvent.TURN): SessionState.RUNNING,
    (SessionState.RUNNING, SessionEvent.TOOL_CALL): SessionState.RUNNING,
    (SessionState.RUNNING, SessionEvent.APPROVAL_REQUIRED): SessionState.AWAITING_APPROVAL,
    (SessionState.RUNNING, SessionEvent.FINISH): SessionState.DONE,
    (SessionState.RUNNING, SessionEvent.STOP): SessionState.STOPPED,
    (SessionState.RUNNING, SessionEvent.FAIL): SessionState.FAILED,
    (SessionState.AWAITING_APPROVAL, SessionEvent.APPROVAL_DECIDED): SessionState.RUNNING,
    (SessionState.AWAITING_APPROVAL, SessionEvent.STOP): SessionState.STOPPED,
    (SessionState.AWAITING_APPROVAL, SessionEvent.FAIL): SessionState.FAILED,
}


class StopReason(StrEnum):
    """The stable vocabulary a session outcome carries.

    ``KILL_SWITCH_ARMED`` and ``AWAITING_APPROVAL`` already exist in the platform taxonomy
    (GH-28); the budget and no-fake-data codes here are runtime-local until that taxonomy
    gains the service as a layer.
    """

    COMPLETED = "COMPLETED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    KILL_SWITCH_ARMED = "KILL_SWITCH_ARMED"
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
    SESSION_TURNS_EXHAUSTED = "SESSION_TURNS_EXHAUSTED"
    SESSION_TOKENS_EXHAUSTED = "SESSION_TOKENS_EXHAUSTED"
    SESSION_COST_EXHAUSTED = "SESSION_COST_EXHAUSTED"
    DAILY_TURNS_EXHAUSTED = "DAILY_TURNS_EXHAUSTED"
    DAILY_TOKENS_EXHAUSTED = "DAILY_TOKENS_EXHAUSTED"
    DAILY_COST_EXHAUSTED = "DAILY_COST_EXHAUSTED"
    TOOL_OUT_OF_SCOPE = "TOOL_OUT_OF_SCOPE"
    UNSOURCED_NUMBER = "UNSOURCED_NUMBER"
    NUMBER_MISMATCH = "NUMBER_MISMATCH"
    MCP_UNREACHABLE = "MCP_UNREACHABLE"
    MODEL_FAILED = "MODEL_FAILED"


class InvalidTransition(RuntimeError):
    def __init__(self, state: SessionState, event: SessionEvent) -> None:
        super().__init__(f"no transition from {state.value} on {event.value}")
        self.state = state
        self.event = event


class Session:
    def __init__(self, session_id: str) -> None:
        self.id = session_id
        self._state = SessionState.IDLE

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def terminal(self) -> bool:
        return self._state in TERMINAL_STATES

    def fire(self, event: SessionEvent) -> SessionState:
        key = (self._state, event)
        try:
            nxt = TRANSITIONS[key]
        except KeyError:
            raise InvalidTransition(self._state, event) from None
        self._state = nxt
        return nxt


__all__ = [
    "InvalidTransition",
    "Session",
    "SessionEvent",
    "SessionState",
    "StopReason",
    "TERMINAL_STATES",
    "TRANSITIONS",
]
