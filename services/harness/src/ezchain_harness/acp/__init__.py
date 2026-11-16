"""Bring an external agent behind the panel over the Agent Client Protocol.

The harness starts Claude Code, Codex or OpenCode as an ACP peer over stdio and is that
agent's client. The agent sees only the platform's own MCP server: no file, shell or network
tool is offered, and a permission request is relayed to the platform's approval authority
rather than granted here. An adapter is listed as supported only after it has passed the
prompt-injection gate.
"""

from __future__ import annotations

from .adapters import (
    ADAPTERS,
    CLAUDE_CODE,
    CODEX,
    OPENCODE,
    PLATFORM_MCP_COMMAND,
    PLATFORM_MCP_NAME,
    SUPPORTED_ADAPTERS,
    AgentLaunch,
    client_capabilities,
    platform_mcp_servers,
    supported_adapters,
)
from .client import (
    AcpClient,
    AcpError,
    EndOfStream,
    PermissionBroker,
    PermissionKind,
    PermissionOption,
    PermissionRequest,
    RpcEndpoint,
    SubprocessRpc,
    launch_agent,
)

__all__ = [
    "ADAPTERS",
    "CLAUDE_CODE",
    "CODEX",
    "OPENCODE",
    "PLATFORM_MCP_COMMAND",
    "PLATFORM_MCP_NAME",
    "SUPPORTED_ADAPTERS",
    "AcpClient",
    "AcpError",
    "AgentLaunch",
    "EndOfStream",
    "PermissionBroker",
    "PermissionKind",
    "PermissionOption",
    "PermissionRequest",
    "RpcEndpoint",
    "SubprocessRpc",
    "client_capabilities",
    "launch_agent",
    "platform_mcp_servers",
    "supported_adapters",
]
