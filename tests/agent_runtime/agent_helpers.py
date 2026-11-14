"""Scripted model and fake MCP server shared by the runtime tests.

Nothing here opens a socket or reads a key: the model is a list of turns and the MCP server
is a dictionary of results.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from ezchain_agent_runtime import (
    BudgetLimits,
    McpTool,
    ModelRequest,
    ModelTurn,
    ToolResult,
)

NOW = datetime(2026, 11, 14, 9, 0, 30, tzinfo=UTC)


class FixedClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


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


def tool(name: str, *, description: str | None = None) -> McpTool:
    return McpTool(
        name=name,
        description=description or f"{name} from the ezCHAIN MCP server",
        input_schema={"type": "object", "properties": {}},
    )


ACCOUNT = tool("ezchain_get_account")
STATE = tool("ezchain_get_state")
APPROVALS = tool("ezchain_list_approvals")
PLACE_ORDER = tool("ezchain_place_order")

SCOPED_TOOLS: tuple[McpTool, ...] = (ACCOUNT, STATE, APPROVALS, PLACE_ORDER)

ACCOUNT_RESULT = ToolResult(
    tool=ACCOUNT.name,
    data={
        "equity": "1000.00",
        "cash": "1000.00",
        "as_of": "2026-11-14T09:00:00Z",
    },
)


class FakeModel:
    def __init__(self, turns: Sequence[ModelTurn | Exception]) -> None:
        self._turns = list(turns)
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelTurn:
        self.requests.append(request)
        if not self._turns:
            raise AssertionError("the loop called the model past the script")
        nxt = self._turns.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class FakeMcp:
    def __init__(
        self,
        tools: Sequence[McpTool] = SCOPED_TOOLS,
        results: Mapping[str, ToolResult | Exception | Callable[[Mapping[str, Any]], ToolResult]]
        | None = None,
        *,
        list_error: Exception | None = None,
        on_call: Callable[[str, Mapping[str, Any]], None] | None = None,
    ) -> None:
        self.tools = tuple(tools)
        self.results = dict(results or {})
        self.list_error = list_error
        self.on_call = on_call
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_tools(self) -> Sequence[McpTool]:
        if self.list_error is not None:
            raise self.list_error
        return self.tools

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> ToolResult:
        self.calls.append((name, dict(arguments)))
        if self.on_call is not None:
            self.on_call(name, arguments)
        result = self.results.get(name)
        if result is None:
            raise AssertionError(f"no fake result for {name}")
        if isinstance(result, Exception):
            raise result
        if callable(result):
            return result(arguments)
        return result
