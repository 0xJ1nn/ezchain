"""OpenAI-compatible chat completions, behind the loop's ``ModelClient``.

"OpenAI-compatible" is the wire format, not one vendor: the base URL is a constructor
argument so the same client speaks to the OpenAI API, a gateway, or a local server that
implements ``/chat/completions``.

| runtime | chat completions |
|---|---|
| system ``Message`` | ``system`` message |
| user / assistant text | ``user`` / ``assistant`` content string |
| assistant ``ToolCall`` | ``tool_calls[].function`` (``arguments`` as JSON text) |
| ``tool`` ``Message`` | ``{"role": "tool", "tool_call_id": ...}`` |
| ``ModelUsage`` | ``usage.prompt_tokens`` / ``usage.completion_tokens`` |

A tool result is its own ``tool`` message addressed by ``tool_call_id`` — the provider's
native shape, not a sentence in the conversation — and it always follows the assistant
message that requested it, because the API refuses a result with no matching call.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, ClassVar

from ezchain_agent_runtime import Message, ModelRequest, ModelTurn, ModelUsage, ToolCall

from .answer import ANSWER_CONTRACT, parse_answer
from .base import ProviderClient, tool_arguments, usage_from
from .errors import ProviderProtocolError

DEFAULT_BASE_URL = "https://api.openai.com/v1"


def _assistant_message(message: Message) -> dict[str, Any]:
    out: dict[str, Any] = {"role": "assistant", "content": message.content or None}
    if message.tool_calls:
        out["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(dict(call.arguments), sort_keys=True),
                },
            }
            for call in message.tool_calls
        ]
    return out


def to_chat_messages(messages: tuple[Message, ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            out.append({"role": "system", "content": message.content})
        elif message.role == "assistant":
            out.append(_assistant_message(message))
        elif message.role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id or "",
                    "content": message.content,
                }
            )
        else:
            out.append({"role": "user", "content": message.content})
    return out


class OpenAICompatibleClient(ProviderClient):
    provider: ClassVar[str] = "openai"
    credential_env: ClassVar[str] = "OPENAI_API_KEY"

    def __init__(self, *, base_url: str = DEFAULT_BASE_URL, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._base_url = base_url.rstrip("/")

    def _endpoint(self) -> str:
        return f"{self._base_url}/chat/completions"

    def _headers(self, secret: str) -> dict[str, str]:
        return {
            "content-type": "application/json",
            "authorization": f"Bearer {secret}",
        }

    def _payload(self, request: ModelRequest) -> dict[str, Any]:
        messages = to_chat_messages(request.messages)
        index = next(
            (position for position, message in enumerate(messages) if message["role"] == "system"),
            None,
        )
        if index is None:
            messages.insert(0, {"role": "system", "content": ANSWER_CONTRACT})
        else:
            messages[index] = {
                "role": "system",
                "content": f"{messages[index]['content']}\n\n{ANSWER_CONTRACT}",
            }
        payload: dict[str, Any] = {"model": self.model_id, "messages": messages}
        tools = [
            {
                "type": "function",
                "function": {
                    "name": item["name"],
                    "description": item.get("description", ""),
                    "parameters": dict(item.get("inputSchema") or {"type": "object"}),
                },
            }
            for item in request.tools
        ]
        if tools:
            payload["tools"] = tools
        return payload

    def _decode(self, body: Mapping[str, Any]) -> ModelTurn:
        choices = self._require(body, "choices", list)
        if not choices or not isinstance(choices[0], Mapping):
            raise ProviderProtocolError("the response has no choices")
        message = choices[0].get("message")
        if not isinstance(message, Mapping):
            raise ProviderProtocolError("the first choice has no message")
        content = message.get("content")
        text = content if isinstance(content, str) else ""
        calls: list[ToolCall] = []
        raw_calls = message.get("tool_calls")
        if isinstance(raw_calls, list):
            for raw in raw_calls:
                if not isinstance(raw, Mapping):
                    raise ProviderProtocolError("a tool call is not an object")
                function = raw.get("function")
                call_id = raw.get("id")
                if not isinstance(function, Mapping) or not isinstance(call_id, str):
                    raise ProviderProtocolError("a tool call has no id or function")
                name = function.get("name")
                if not isinstance(name, str):
                    raise ProviderProtocolError("a tool call has no function name")
                calls.append(
                    ToolCall(
                        id=call_id,
                        name=name,
                        arguments=tool_arguments(function.get("arguments")),
                    )
                )
        prompt_tokens, completion_tokens = usage_from(body.get("usage"))
        return ModelTurn(
            text=text,
            tool_calls=tuple(calls),
            answer=None if calls else parse_answer(text),
            usage=ModelUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
        )


__all__ = ["DEFAULT_BASE_URL", "OpenAICompatibleClient", "to_chat_messages"]
