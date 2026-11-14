"""A transport that answers from a script, and the objects a client needs.

Nothing here opens a socket: the transport is a list of responses and the credential is an
environment variable the fixture sets. Every test in this directory runs the real mapping
code with no key and no network.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from ezchain_agent_runtime import BudgetLimits, McpTool, ToolResult
from ezchain_harness import HttpRequest, HttpResponse, ModelPricing, SessionRecord

PINNED_MODEL = "claude-sonnet-4-5"
OPENAI_MODEL = "gpt-4o-mini"
KEY = "sk-ant-api03-TOPSECRETVALUE-never-log-me-9f2a"

PRICING = ModelPricing(
    input_per_million=Decimal("3.00"),
    output_per_million=Decimal("15.00"),
)


class ScriptedTransport:
    """One response per call, or a raised exception; keeps every request it was given."""

    def __init__(self, script: Sequence[HttpResponse | Exception]) -> None:
        self._script = list(script)
        self.requests: list[HttpRequest] = []

    def __call__(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if not self._script:
            raise AssertionError("the client called the transport past the script")
        nxt = self._script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    @property
    def calls(self) -> int:
        return len(self.requests)


def record(
    *, session_id: str = "s1", provider: str = "anthropic", model_id: str = PINNED_MODEL
) -> SessionRecord:
    return SessionRecord(session_id=session_id, provider=provider, model_id=model_id)


def anthropic_tool_use(
    *,
    call_id: str = "toolu_1",
    name: str = "ezchain_place_order",
    arguments: Mapping[str, Any] | None = None,
    text: str = "",
    prompt_tokens: int = 120,
    completion_tokens: int = 30,
) -> HttpResponse:
    content: list[dict[str, Any]] = []
    if text:
        content.append({"type": "text", "text": text})
    content.append(
        {
            "type": "tool_use",
            "id": call_id,
            "name": name,
            "input": dict(arguments or {"symbol": "ETH/USDC", "notional": "10"}),
        }
    )
    return HttpResponse(
        status=200,
        body={
            "type": "message",
            "role": "assistant",
            "content": content,
            "stop_reason": "tool_use",
            "usage": {"input_tokens": prompt_tokens, "output_tokens": completion_tokens},
        },
    )


def anthropic_text(
    text: str, *, prompt_tokens: int = 200, completion_tokens: int = 40
) -> HttpResponse:
    return HttpResponse(
        status=200,
        body={
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": prompt_tokens, "output_tokens": completion_tokens},
        },
    )


def openai_tool_call(
    *,
    call_id: str = "call_1",
    name: str = "ezchain_place_order",
    arguments: str = '{"symbol":"ETH/USDC","notional":"10"}',
    prompt_tokens: int = 90,
    completion_tokens: int = 20,
) -> HttpResponse:
    return HttpResponse(
        status=200,
        body={
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {"name": name, "arguments": arguments},
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        },
    )


def openai_text(text: str, *, prompt_tokens: int = 80, completion_tokens: int = 25) -> HttpResponse:
    return HttpResponse(
        status=200,
        body={
            "id": "chatcmpl-2",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": text},
                }
            ],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        },
    )


NOW = datetime(2026, 11, 14, 9, 0, 30, tzinfo=UTC)


class FixedClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make_limits(
    *,
    session_turns: int = 8,
    session_tokens: int = 10_000,
    session_cost_usd: Decimal = Decimal("1"),
    daily_turns: int = 50,
    daily_tokens: int = 100_000,
    daily_cost_usd: Decimal = Decimal("10"),
) -> BudgetLimits:
    return BudgetLimits(
        session_turns=session_turns,
        session_tokens=session_tokens,
        session_cost_usd=session_cost_usd,
        daily_turns=daily_turns,
        daily_tokens=daily_tokens,
        daily_cost_usd=daily_cost_usd,
    )


PLACE_ORDER = McpTool(
    name="ezchain_place_order",
    description="place a paper order",
    input_schema={
        "type": "object",
        "properties": {"symbol": {"type": "string"}, "notional": {"type": "string"}},
    },
)
ACCOUNT = McpTool(
    name="ezchain_get_account",
    description="read the paper account",
    input_schema={"type": "object", "properties": {}},
)


class FakeMcp:
    """A dictionary of tool results that records every call it was asked to make."""

    def __init__(
        self,
        results: Mapping[str, ToolResult] | None = None,
        *,
        tools: Sequence[McpTool] = (PLACE_ORDER, ACCOUNT),
        on_call: Callable[[str, Mapping[str, Any]], None] | None = None,
    ) -> None:
        self.tools = tuple(tools)
        self.results = dict(results or {})
        self.on_call = on_call
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_tools(self) -> Sequence[McpTool]:
        return self.tools

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> ToolResult:
        self.calls.append((name, dict(arguments)))
        if self.on_call is not None:
            self.on_call(name, arguments)
        return self.results.get(name, ToolResult(tool=name, data={}))
