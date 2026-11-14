"""The headless trace: one line per step, printed as the loop takes it.

The trace wraps the two seams the loop already injects — the model client and the MCP
toolbox — and prints what crosses them. It decides nothing. Whether an order waits for a
person, whether a budget is spent and whether the kill switch stops the session are all
:class:`~ezchain_agent_runtime.AgentLoop`'s decisions; the final line is rendered from the
:class:`~ezchain_agent_runtime.SessionOutcome` the loop returned. There is no second loop
here that could disagree with the panel's.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, TextIO

from ezchain_agent_runtime import (
    McpTool,
    McpToolbox,
    ModelClient,
    ModelRequest,
    ModelTurn,
    SessionOutcome,
    SourcedAnswer,
    ToolResult,
)

PREFIX = "ezchain-harness: "
SUMMARY_LIMIT = 240


def _value(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"))


def summarize(data: Any, *, limit: int = SUMMARY_LIMIT) -> str:
    """One bounded line for a tool result, so a large payload cannot drown the trace."""
    if isinstance(data, Mapping):
        text = ", ".join(
            f"{key}={_value(value)}"
            for key, value in sorted(data.items(), key=lambda item: str(item[0]))
        )
    else:
        text = _value(data)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


class Trace:
    """The stdout writer. Every line carries the command's prefix and is flushed."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def line(self, text: str) -> None:
        print(f"{PREFIX}{text}", file=self._stream, flush=True)

    def answer(self, answer: SourcedAnswer) -> None:
        if answer.summary:
            self.line(f"answer {answer.summary}")
        for number in answer.numbers:
            self.line(
                f"number {number.label} {number.value} "
                f"(source {number.tool}.{number.path}, age {number.age_seconds}s)"
            )

    def end(self, outcome: SessionOutcome) -> None:
        usage = outcome.usage
        fields = [
            f"end {outcome.reason.value}",
            f"state={outcome.state.value}",
            f"tools={len(outcome.observations)}",
            f"turns={usage.turns}",
            f"tokens={usage.tokens}",
            f"cost_usd={usage.cost_usd:.2f}",
        ]
        if outcome.detail:
            fields.append(f"detail={outcome.detail}")
        self.line(" ".join(fields))


class TracedModel:
    """A :class:`ModelClient` that announces each turn before the model answers."""

    def __init__(self, inner: ModelClient, trace: Trace) -> None:
        self._inner = inner
        self._trace = trace
        self._turns = 0

    def complete(self, request: ModelRequest) -> ModelTurn:
        self._turns += 1
        self._trace.line(f"turn {self._turns}")
        return self._inner.complete(request)


class TracedToolbox:
    """An :class:`McpToolbox` that prints the surface, every call and every result.

    It forwards both arguments and results untouched and never inspects a result for a
    pending approval: the loop reads that, and the trace only reports what the loop decided.
    """

    def __init__(self, inner: McpToolbox, trace: Trace) -> None:
        self._inner = inner
        self._trace = trace

    def list_tools(self) -> Sequence[McpTool]:
        tools = self._inner.list_tools()
        names = ", ".join(tool.name for tool in tools)
        self._trace.line(f"tools {len(tools)}: {names}")
        return tools

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> ToolResult:
        self._trace.line(f"call {name} {_value(arguments)}")
        try:
            result = self._inner.call_tool(name, arguments)
        except Exception as exc:
            self._trace.line(f"result {name} error {exc}")
            raise
        verdict = "error" if result.is_error else "ok"
        self._trace.line(f"result {name} {verdict} {summarize(result.data)}")
        return result


__all__ = ["PREFIX", "SUMMARY_LIMIT", "Trace", "TracedModel", "TracedToolbox", "summarize"]
