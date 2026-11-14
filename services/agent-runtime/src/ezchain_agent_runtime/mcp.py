"""The runtime's only door to ezCHAIN: an MCP tool surface.

The loop is handed a :class:`McpToolbox` and builds a :class:`ToolSurface` from the server's
own ``tools/list`` response. There is no registry of built-in tools and no other caller: a
name the server did not list cannot be invoked. Avoiding shell, URL fetch and file access is
therefore structural, not a filter applied afterwards.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


class McpError(RuntimeError):
    """The MCP server could not be reached or answered with a protocol error."""


class ToolSurfaceError(McpError):
    """The server's tool list is unusable (empty name or a duplicate)."""


class ToolOutOfScope(LookupError):
    """A call named a tool the server did not list for this session's key."""

    def __init__(self, name: str, available: Sequence[str]) -> None:
        super().__init__(f"{name!r} is not an MCP tool for this key; available: {list(available)}")
        self.name = name
        self.available = tuple(available)


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True)
class ToolResult:
    tool: str
    data: Any = None
    is_error: bool = False


class McpToolbox(Protocol):
    """One child ``ezchain-mcp`` process, addressed by JSON-RPC."""

    def list_tools(self) -> Sequence[McpTool]: ...

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> ToolResult: ...


@dataclass(frozen=True)
class ToolSurface:
    """Exactly the tools the MCP server listed, in its order."""

    tools: tuple[McpTool, ...]

    @classmethod
    def from_toolbox(cls, toolbox: McpToolbox) -> ToolSurface:
        try:
            tools = tuple(toolbox.list_tools())
        except McpError:
            raise
        except Exception as exc:  # transport-level failures fail the session closed
            raise McpError(f"tools/list failed: {exc}") from exc
        names = [tool.name for tool in tools]
        if any(not name for name in names):
            raise ToolSurfaceError("the MCP server listed a tool with no name")
        if len(set(names)) != len(names):
            raise ToolSurfaceError(f"the MCP server listed duplicate tools: {names}")
        return cls(tools)

    def names(self) -> tuple[str, ...]:
        return tuple(tool.name for tool in self.tools)

    def __contains__(self, name: object) -> bool:
        return any(tool.name == name for tool in self.tools)

    def require(self, name: str) -> McpTool:
        for tool in self.tools:
            if tool.name == name:
                return tool
        raise ToolOutOfScope(name, self.names())

    def model_tools(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": dict(tool.input_schema),
            }
            for tool in self.tools
        )


__all__ = [
    "McpError",
    "McpTool",
    "McpToolbox",
    "ToolOutOfScope",
    "ToolResult",
    "ToolSurface",
    "ToolSurfaceError",
]
