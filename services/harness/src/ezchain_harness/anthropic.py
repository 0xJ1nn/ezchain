"""Anthropic's Messages API, behind the loop's :class:`~ezchain_agent_runtime.ModelClient`.

The mapping, both ways:

| runtime | Anthropic |
|---|---|
| system ``Message`` | top-level ``system`` string |
| user / assistant text | ``text`` content block |
| assistant ``ToolCall`` | ``tool_use`` block (``id``, ``name``, ``input``) |
| ``tool`` ``Message`` | a ``user`` message holding ``tool_result`` blocks |
| ``ModelUsage`` | ``usage.input_tokens`` / ``usage.output_tokens`` |

A tool result is sent as Anthropic's own ``tool_result`` block, never as prose: an assistant
turn's ``tool_use`` id is answered with a ``tool_result`` carrying that id, and consecutive
tool messages are merged into one user turn because the API requires every result for a turn
to arrive together, immediately after it.

The final text is parsed as the loop's answer object (see :mod:`.answer`); a turn that stops
on ``tool_use`` is returned as tool calls, and prose that is neither is returned as text so the
loop refuses it rather than the client inventing an answer.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from ezchain_agent_runtime import Message, ModelRequest, ModelTurn, ModelUsage, ToolCall

from .answer import ANSWER_CONTRACT, parse_answer
from .base import ProviderClient, tool_arguments, usage_from
from .errors import ProviderProtocolError

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_BASE_URL = "https://api.anthropic.com"


def _tool_result_message(message: Message) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": message.tool_call_id or "",
        "content": message.content,
    }


def _text_blocks(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": text}] if text else []


def to_anthropic_messages(messages: tuple[Message, ...]) -> tuple[str, list[dict[str, Any]]]:
    """``(system, messages)`` in the Messages API's shape."""
    system: list[str] = []
    out: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            system.append(message.content)
            continue
        if message.role == "tool":
            block = _tool_result_message(message)
            if out and out[-1]["role"] == "user" and _is_tool_results(out[-1]):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
            continue
        if message.role == "assistant":
            blocks = _text_blocks(message.content)
            for call in message.tool_calls:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": dict(call.arguments),
                    }
                )
            if not blocks:
                continue
            out.append({"role": "assistant", "content": blocks})
            continue
        out.append({"role": "user", "content": _text_blocks(message.content)})
    return "\n\n".join(system), out


def _is_tool_results(message: Mapping[str, Any]) -> bool:
    content = message.get("content")
    return (
        isinstance(content, list)
        and bool(content)
        and isinstance(content[0], Mapping)
        and (content[0].get("type") == "tool_result")
    )


class AnthropicClient(ProviderClient):
    provider: ClassVar[str] = "anthropic"
    credential_env: ClassVar[str] = "ANTHROPIC_API_KEY"

    def __init__(self, *, base_url: str = DEFAULT_BASE_URL, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._base_url = base_url.rstrip("/")

    def _endpoint(self) -> str:
        return f"{self._base_url}/v1/messages"

    def _headers(self, secret: str) -> dict[str, str]:
        return {
            "content-type": "application/json",
            "x-api-key": secret,
            "anthropic-version": ANTHROPIC_VERSION,
        }

    def _payload(self, request: ModelRequest) -> dict[str, Any]:
        system, messages = to_anthropic_messages(request.messages)
        payload: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": 4096,
            "system": f"{system}\n\n{ANSWER_CONTRACT}" if system else ANSWER_CONTRACT,
            "messages": messages,
        }
        tools = [
            {
                "name": item["name"],
                "description": item.get("description", ""),
                "input_schema": dict(item.get("inputSchema") or {"type": "object"}),
            }
            for item in request.tools
        ]
        if tools:
            payload["tools"] = tools
        return payload

    def _decode(self, body: Mapping[str, Any]) -> ModelTurn:
        content = self._require(body, "content", list)
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in content:
            if not isinstance(block, Mapping):
                raise ProviderProtocolError("a content block is not an object")
            kind = block.get("type")
            if kind == "text":
                text = block.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
            elif kind == "tool_use":
                call_id, name = block.get("id"), block.get("name")
                if not isinstance(call_id, str) or not isinstance(name, str):
                    raise ProviderProtocolError("a tool_use block has no id or name")
                calls.append(
                    ToolCall(id=call_id, name=name, arguments=tool_arguments(block.get("input")))
                )
        prompt_tokens, completion_tokens = usage_from(body.get("usage"))
        text = "".join(text_parts)
        return ModelTurn(
            text=text,
            tool_calls=tuple(calls),
            answer=None if calls else parse_answer(text),
            usage=ModelUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
        )


__all__ = ["ANTHROPIC_VERSION", "AnthropicClient", "DEFAULT_BASE_URL", "to_anthropic_messages"]
