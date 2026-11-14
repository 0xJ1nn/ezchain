"""ezCHAIN's built-in agent runtime.

One loop, one model client, the platform's MCP tools, and hard budgets. The runtime owns no
money capability: its only egress is the injected :class:`~.mcp.McpToolbox` (one child
``ezchain-mcp`` holding a scoped key), and every number in a final answer is resolved
against a tool result recorded in the same session.
"""

from __future__ import annotations

from .answers import (
    Answer,
    AnswerError,
    NumberClaim,
    NumberMismatch,
    Observation,
    SourcedAnswer,
    SourcedNumber,
    UnsourcedNumber,
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
from .loop import DEFAULT_SYSTEM_PROMPT, AgentLoop, RuntimeConfig, SessionOutcome
from .mcp import (
    McpError,
    McpTool,
    McpToolbox,
    ToolOutOfScope,
    ToolResult,
    ToolSurface,
    ToolSurfaceError,
)
from .model import Message, ModelClient, ModelRequest, ModelTurn, ModelUsage, ToolCall
from .session import (
    TERMINAL_STATES,
    TRANSITIONS,
    InvalidTransition,
    Session,
    SessionEvent,
    SessionState,
    StopReason,
)

__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "TERMINAL_STATES",
    "TRANSITIONS",
    "AgentLoop",
    "Answer",
    "AnswerError",
    "BudgetExhausted",
    "BudgetLimits",
    "Clock",
    "DailyLedger",
    "InMemoryKillSwitch",
    "InvalidTransition",
    "KillSwitch",
    "McpError",
    "McpTool",
    "McpToolbox",
    "Message",
    "ModelClient",
    "ModelRequest",
    "ModelUsage",
    "ModelTurn",
    "NumberClaim",
    "NumberMismatch",
    "Observation",
    "RuntimeConfig",
    "Session",
    "SessionBudget",
    "SessionEvent",
    "SessionOutcome",
    "SessionState",
    "SourcedAnswer",
    "SourcedNumber",
    "StopReason",
    "ToolCall",
    "ToolOutOfScope",
    "ToolResult",
    "ToolSurface",
    "ToolSurfaceError",
    "UnsourcedNumber",
    "Usage",
    "resolve_answer",
    "utcnow",
]
