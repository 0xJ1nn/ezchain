"""The ACP client side: the boundary between an external agent and the platform.

The external agent is a peer that sends JSON-RPC requests to this client. Two of those
requests matter for safety. ``session/request_permission`` is never granted here: it is
handed to an injected approval authority, and only a one-shot choice that authority returns
is relayed to the agent. Everything else the agent can ask of its client — reading or writing
a file, starting a terminal, fetching a URL — is refused, because the platform gives the
agent no tool other than its own MCP server.

The transport is a seam. Tests pass an in-memory RPC endpoint; the runtime passes a
:class:`SubprocessRpc` that starts one of the declared adapters with stdin and stdout pipes.
No shell is used and no credential is read by this module.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from .adapters import AgentLaunch, client_capabilities, platform_mcp_servers

METHOD_NOT_FOUND = -32601
CLIENT_NAME = "ezchain-harness"


class PermissionKind(StrEnum):
    """The one-shot and standing choices an ACP agent may put in a permission request."""

    ALLOW_ONCE = "allow_once"
    ALLOW_ALWAYS = "allow_always"
    REJECT_ONCE = "reject_once"
    REJECT_ALWAYS = "reject_always"


@dataclass(frozen=True)
class PermissionOption:
    """A choice the agent offered, reduced to a one-shot kind."""

    option_id: str
    kind: PermissionKind
    name: str


@dataclass(frozen=True)
class PermissionRequest:
    """What the approval authority needs in order to decide.

    The harness does not decide and does not hold a number or an order: it carries the
    agent's tool name and its one-shot options to the authority that owns approvals.
    """

    session_id: str
    tool_name: str
    title: str
    options: tuple[PermissionOption, ...]


class PermissionBroker(Protocol):
    """The platform's approval authority, injected by the caller."""

    def decide(self, request: PermissionRequest) -> str: ...


class RpcEndpoint(Protocol):
    """A newline-delimited JSON-RPC peer over any byte channel."""

    def send(self, message: dict[str, Any]) -> None: ...

    def receive(self) -> dict[str, Any]: ...


class AcpError(RuntimeError):
    """The ACP peer broke the protocol or closed the channel."""


class EndOfStream(AcpError):
    """The agent closed its end of the channel."""


class SubprocessRpc:
    """An RPC endpoint backed by one agent process started without a shell."""

    def __init__(self, command: tuple[str, ...]) -> None:
        self._process = subprocess.Popen(
            list(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
        )

    def send(self, message: dict[str, Any]) -> None:
        if self._process.stdin is None:
            raise AcpError("the agent process has no stdin")
        self._process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        self._process.stdin.flush()

    def receive(self) -> dict[str, Any]:
        if self._process.stdout is None:
            raise AcpError("the agent process has no stdout")
        line = self._process.stdout.readline()
        if not line:
            raise EndOfStream("the agent closed stdout")
        try:
            message = json.loads(line)
        except ValueError as exc:
            raise AcpError("the agent sent malformed JSON") from exc
        if not isinstance(message, dict):
            raise AcpError("the agent sent a JSON-RPC message that is not an object")
        return message

    def close(self) -> None:
        if self._process.stdin is not None:
            self._process.stdin.close()
        self._process.wait()


def launch_agent(adapter: AgentLaunch) -> SubprocessRpc:
    """Start ``adapter`` as an ACP peer over stdio."""
    return SubprocessRpc(adapter.command)


class AcpClient:
    """Speak the client half of ACP and keep the agent inside its boundary."""

    def __init__(
        self,
        endpoint: RpcEndpoint,
        *,
        broker: PermissionBroker | None,
        adapter: AgentLaunch | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._broker = broker
        self._adapter = adapter
        self._next_id = 1

    @property
    def adapter(self) -> AgentLaunch | None:
        return self._adapter

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._endpoint.send(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        )
        while True:
            message = self._endpoint.receive()
            if "method" in message:
                if "id" in message:
                    self._handle_agent_request(message)
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise AcpError(f"{method} was refused: {message['error']}")
            result = message.get("result", {})
            return result if isinstance(result, dict) else {}

    def _handle_agent_request(self, message: dict[str, Any]) -> None:
        if message["method"] == "session/request_permission":
            self._handle_permission(message)
            return
        self._endpoint.send(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "error": {"code": METHOD_NOT_FOUND, "message": "capability not available"},
            }
        )

    def _handle_permission(self, message: dict[str, Any]) -> None:
        params = message.get("params") or {}
        options: list[PermissionOption] = []
        for raw in params.get("options") or []:
            if not isinstance(raw, dict):
                continue
            raw_kind = raw.get("kind")
            if not isinstance(raw_kind, str):
                continue
            try:
                kind = PermissionKind(raw_kind)
            except ValueError:
                continue
            if kind in (PermissionKind.ALLOW_ALWAYS, PermissionKind.REJECT_ALWAYS):
                continue
            option_id = raw.get("optionId")
            if not isinstance(option_id, str) or not option_id:
                continue
            options.append(PermissionOption(option_id, kind, str(raw.get("name") or "")))

        tool_call = params.get("toolCall") or {}
        request = PermissionRequest(
            session_id=str(params.get("sessionId") or ""),
            tool_name=str(tool_call.get("name") or ""),
            title=str(tool_call.get("title") or ""),
            options=tuple(options),
        )

        choice = ""
        if self._broker is not None:
            try:
                choice = self._broker.decide(request) or ""
            except Exception:
                choice = ""

        granted = next(
            (
                option
                for option in options
                if option.option_id == choice and option.kind is PermissionKind.ALLOW_ONCE
            ),
            None,
        )
        result: dict[str, Any] = (
            {"optionId": granted.option_id} if granted is not None else {"cancelled": True}
        )
        self._endpoint.send({"jsonrpc": "2.0", "id": message["id"], "result": result})

    def initialize(self) -> dict[str, Any]:
        return self._request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": client_capabilities(),
                "clientInfo": {"name": CLIENT_NAME, "version": "0.1.0"},
            },
        )

    def new_session(self, cwd: str = ".") -> dict[str, Any]:
        return self._request(
            "session/new",
            {"cwd": cwd, "mcpServers": [dict(server) for server in platform_mcp_servers()]},
        )

    def prompt(self, session_id: str, text: str) -> dict[str, Any]:
        return self._request(
            "session/prompt",
            {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]},
        )


__all__ = [
    "CLIENT_NAME",
    "METHOD_NOT_FOUND",
    "AcpClient",
    "AcpError",
    "EndOfStream",
    "PermissionBroker",
    "PermissionKind",
    "PermissionOption",
    "PermissionRequest",
    "RpcEndpoint",
    "SubprocessRpc",
    "launch_agent",
]
