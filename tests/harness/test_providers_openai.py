"""The OpenAI-compatible chat client: the wire shapes, both directions.

"Compatible" is the point — the base URL is a constructor argument, so these tests point the
same client at a gateway host and never at a vendor.
"""

from __future__ import annotations

import json
from decimal import Decimal

from ezchain_agent_runtime import Message, ModelRequest, ModelTurn, ToolCall
from ezchain_harness import HttpResponse, OpenAICompatibleClient

from .providers_helpers import (
    OPENAI_MODEL,
    PRICING,
    ScriptedTransport,
    openai_text,
    openai_tool_call,
    record,
)

TOOLS: tuple[dict[str, object], ...] = (
    {
        "name": "ezchain_place_order",
        "description": "place a paper order",
        "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}}},
    },
)


def _client(transport: ScriptedTransport) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        model_id=OPENAI_MODEL,
        pricing=PRICING,
        record=record(provider="openai", model_id=OPENAI_MODEL),
        transport=transport,
        base_url="https://gateway.test/v1",
    )


def _request() -> ModelRequest:
    return ModelRequest(
        messages=(
            Message(role="system", content="You are ezCHAIN's agent."),
            Message(role="user", content="place a small order"),
        ),
        tools=TOOLS,
    )


def test_a_function_call_becomes_a_tool_call_with_parsed_arguments() -> None:
    transport = ScriptedTransport([openai_tool_call()])

    turn = _client(transport).complete(_request())

    assert isinstance(turn, ModelTurn)
    assert turn.tool_calls == (
        ToolCall(
            id="call_1",
            name="ezchain_place_order",
            arguments={"symbol": "ETH/USDC", "notional": "10"},
        ),
    )
    assert turn.answer is None
    sent = transport.requests[0]
    assert sent.url == "https://gateway.test/v1/chat/completions"
    assert sent.json["model"] == OPENAI_MODEL
    assert sent.json["tools"][0]["function"]["parameters"]["type"] == "object"
    assert sent.headers["authorization"].startswith("Bearer ")


def test_a_tool_result_is_its_own_tool_message_naming_the_call() -> None:
    transport = ScriptedTransport([openai_text('{"summary": "The order is in."}')])
    request = ModelRequest(
        messages=(
            Message(role="system", content="system"),
            Message(role="user", content="place a small order"),
            Message(
                role="assistant",
                content="",
                tool_calls=(ToolCall(id="call_7", name="ezchain_place_order", arguments={"a": 1}),),
            ),
            Message(role="tool", content='{"order_id":"o1"}', tool_call_id="call_7"),
        ),
        tools=TOOLS,
    )

    _client(transport).complete(request)

    messages = transport.requests[0].json["messages"]
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assistant = messages[2]
    assert assistant["content"] is None
    assert assistant["tool_calls"] == [
        {
            "id": "call_7",
            "type": "function",
            "function": {
                "name": "ezchain_place_order",
                "arguments": json.dumps({"a": 1}, sort_keys=True),
            },
        }
    ]
    assert messages[3] == {
        "role": "tool",
        "tool_call_id": "call_7",
        "content": '{"order_id":"o1"}',
    }


def test_the_answer_contract_is_stated_in_a_system_message() -> None:
    transport = ScriptedTransport([openai_text('{"summary": "nothing"}')])
    request = ModelRequest(messages=(Message(role="user", content="hi"),), tools=TOOLS)

    _client(transport).complete(request)

    messages = transport.requests[0].json["messages"]
    assert messages[0]["role"] == "system"
    assert "claims" in messages[0]["content"]


def test_usage_is_counted_and_priced() -> None:
    transport = ScriptedTransport(
        [openai_text('{"summary": "done"}', prompt_tokens=1_000_000, completion_tokens=1_000_000)]
    )

    turn = _client(transport).complete(_request())

    assert turn.usage.total_tokens == 2_000_000
    assert turn.usage.cost_usd == Decimal("18.000000")


def test_text_that_is_not_the_answer_object_is_returned_as_plain_text() -> None:
    transport = ScriptedTransport([openai_text("sure, everything is fine")])

    turn = _client(transport).complete(_request())

    assert turn.answer is None
    assert turn.text == "sure, everything is fine"


def test_a_choice_with_neither_content_nor_calls_decodes_to_an_empty_turn() -> None:
    transport = ScriptedTransport(
        [
            HttpResponse(
                status=200,
                body={
                    "choices": [{"message": {"role": "assistant", "content": None}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 0},
                },
            )
        ]
    )

    turn = _client(transport).complete(_request())

    assert turn.text == ""
    assert turn.tool_calls == ()
    assert turn.answer is None
