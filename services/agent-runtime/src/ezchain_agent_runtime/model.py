"""The model seam.

The loop never talks to a provider directly: it hands a :class:`ModelClient` the message
list and the MCP tool definitions, and reads back one turn. Tests supply a scripted client,
so no test needs a network call or a provider key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from .answers import Answer


@dataclass(frozen=True)
class Message:
    role: str
    content: str
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.prompt_tokens < 0 or self.completion_tokens < 0:
            raise ValueError("token counts cannot be negative")
        if self.cost_usd < 0:
            raise ValueError("cost cannot be negative")

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class ModelRequest:
    messages: tuple[Message, ...]
    tools: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ModelTurn:
    """One model response: prose, tool calls, or a final structured answer."""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    answer: Answer | None = None
    usage: ModelUsage = ModelUsage()


class ModelClient(Protocol):
    def complete(self, request: ModelRequest) -> ModelTurn: ...


__all__ = [
    "Message",
    "ModelClient",
    "ModelRequest",
    "ModelTurn",
    "ToolCall",
    "ModelUsage",
]
