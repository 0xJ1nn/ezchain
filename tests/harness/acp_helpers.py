"""In-memory Agent Client Protocol peers for the harness tests.

Everything here is a plain object speaking the protocol's newline-delimited JSON-RPC shape.
No test starts a subprocess, opens a socket or reads a provider key: the fake agent replays
whatever attack messages a case gives it and records every message the client sends back.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from typing import Any


class EndOfScript(RuntimeError):
    """The fake agent has no more messages to hand the client."""


def option(option_id: str, kind: str, name: str) -> dict[str, str]:
    """One ACP permission option in the wire shape the agent sends."""
    return {"optionId": option_id, "kind": kind, "name": name}


def permission_request(
    req_id: int,
    options: list[dict[str, str]],
    *,
    session_id: str = "session-1",
    tool_name: str = "run_command",
    title: str = "Run a command",
) -> dict[str, Any]:
    """An agent-originated ``session/request_permission`` request."""
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "session/request_permission",
        "params": {
            "sessionId": session_id,
            "toolCall": {"name": tool_name, "title": title},
            "options": options,
        },
    }


def capability_request(
    req_id: int, method: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    """An agent-originated request for a client capability such as ``fs`` or ``terminal``."""
    return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}


class FakeAgent:
    """An RPC endpoint that answers setup requests and replays scripted attacks on prompt.

    The fake agent chooses the ids of its own requests. The ids the client chooses for its
    requests are read from the recorded messages, so the final prompt response always carries
    the right correlation id.
    """

    def __init__(self, *, attacks: list[dict[str, Any]] | None = None) -> None:
        self.client_messages: list[dict[str, Any]] = []
        self.attacks = list(attacks or [])
        self._outgoing: deque[dict[str, Any]] = deque()

    def send(self, message: dict[str, Any]) -> None:
        self.client_messages.append(dict(message))
        method = message.get("method")
        msg_id = message.get("id")
        if method == "initialize":
            self._outgoing.append(
                {"jsonrpc": "2.0", "id": msg_id, "result": {"protocolVersion": 1}}
            )
        elif method == "session/new":
            self._outgoing.append(
                {"jsonrpc": "2.0", "id": msg_id, "result": {"sessionId": "session-1"}}
            )
        elif method == "session/prompt":
            for attack in self.attacks:
                self._outgoing.append(dict(attack))
            self._outgoing.append(
                {"jsonrpc": "2.0", "id": msg_id, "result": {"stopReason": "end_turn"}}
            )

    def receive(self) -> dict[str, Any]:
        if not self._outgoing:
            raise EndOfScript("the fake agent has no message queued")
        return self._outgoing.popleft()

    def answers(self) -> list[dict[str, Any]]:
        """Client messages that answer one of the agent's requests (a result or an error)."""
        return [m for m in self.client_messages if "id" in m and "method" not in m]


class RecordingBroker:
    """A stand-in for the platform's approval authority, with scripted one-shot decisions."""

    def __init__(self, decisions: list[str] | None = None) -> None:
        self.decisions = list(decisions or [])
        self.requests: list[Any] = []

    def decide(self, request: Any) -> str:
        self.requests.append(request)
        return self.decisions.pop(0) if self.decisions else ""


class RefusingBroker:
    """An authority that is unreachable; every permission must fail closed."""

    def __init__(self) -> None:
        self.requests: list[Any] = []

    def decide(self, request: Any) -> str:
        self.requests.append(request)
        raise RuntimeError("the approval service is unreachable")


def start_and_prompt(
    client: Any,
    agent: FakeAgent,
    text: str = "summarise paper equity",
) -> None:
    """Drive a client through initialize, a new session and one prompt."""
    client.initialize()
    client.new_session()
    client.prompt("session-1", text)


def assert_secret_absent(agent: FakeAgent, secret: str) -> None:
    """No message the client sent may carry a credential, even disguised in JSON."""
    sent = repr(agent.client_messages)
    assert secret not in sent, "a credential reached the external agent"


GateResult = Callable[[Any], bool]
