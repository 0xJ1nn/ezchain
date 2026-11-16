"""The external agents the harness can start, and the surface each one is given.

An adapter is only a launch recipe: how to start one agent process and speak ACP to it over
stdio. It has no say in what the agent may reach. The platform's MCP server is attached by the
client on every session, the client advertises no file or terminal capability, and an adapter
is listed as supported only after the prompt-injection gate has passed for it.

The two npm bridges are pinned to the reviewed versions. A floating tag would let a future
release change the agent's capabilities without a review, which is the same mistake as an
unpinned model id.
"""

from __future__ import annotations

from dataclasses import dataclass

PLATFORM_MCP_NAME = "ezchain"
PLATFORM_MCP_COMMAND = "ezchain-mcp"


@dataclass(frozen=True)
class AgentLaunch:
    """How to start one external agent as an ACP peer."""

    name: str
    command: tuple[str, ...]
    injection_verified: bool = False


CLAUDE_CODE = AgentLaunch(
    name="claude-code",
    command=("npx", "-y", "@agentclientprotocol/claude-agent-acp@0.104.1"),
    injection_verified=True,
)

CODEX = AgentLaunch(
    name="codex",
    command=("npx", "-y", "@agentclientprotocol/codex-acp@2.6.0"),
    injection_verified=True,
)

OPENCODE = AgentLaunch(
    name="opencode",
    command=("opencode", "acp"),
    injection_verified=True,
)

ADAPTERS: tuple[AgentLaunch, ...] = (CLAUDE_CODE, CODEX, OPENCODE)


def platform_mcp_servers() -> tuple[dict[str, object], ...]:
    """The single MCP server an external agent is allowed to see.

    No environment is passed here: the scoped key the child needs lives in the child's own
    environment. Putting it in this config would hand it to the external agent.
    """
    return ({"name": PLATFORM_MCP_NAME, "command": PLATFORM_MCP_COMMAND, "args": []},)


def client_capabilities() -> dict[str, object]:
    """What the harness offers the agent as an ACP client.

    The MCP server is the one door. There is deliberately no ``fs`` or ``terminal`` entry, so
    an agent that asks to read a file or run a shell command gets a protocol error instead.
    """
    return {"mcp": True}


def supported_adapters() -> tuple[str, ...]:
    """The adapters that may be offered to users, in launch order."""
    return tuple(adapter.name for adapter in ADAPTERS if adapter.injection_verified)


SUPPORTED_ADAPTERS = supported_adapters()


__all__ = [
    "ADAPTERS",
    "CLAUDE_CODE",
    "CODEX",
    "OPENCODE",
    "PLATFORM_MCP_COMMAND",
    "PLATFORM_MCP_NAME",
    "SUPPORTED_ADAPTERS",
    "AgentLaunch",
    "client_capabilities",
    "platform_mcp_servers",
    "supported_adapters",
]
