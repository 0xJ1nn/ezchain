"""The Anthropic Messages client: the wire shapes, both directions.

Every test drives the real client through :class:`ScriptedTransport`; nothing here opens a
socket or reads a real key.
"""

from __future__ import annotations

from decimal import Decimal

from providers_helpers import (
    PINNED_MODEL,
    PRICING,
    ScriptedTransport,
    anthropic_text,
    anthropic_tool_use,
    record,
)

from ezchain_agent_runtime import Message, ModelRequest, ModelTurn, ToolCall
from ezchain_harness import AnthropicClient, HttpResponse

TOOLS: tuple[dict[str, object], ...] = (
    {
        "name": "ezchain_place_order",
        "description": "place a paper order",
        "inputSchema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
        },
    },
)


def _client(transport: ScriptedTransport) -> AnthropicClient:
    return AnthropicClient(
        model_id=PINNED_MODEL,
        pricing=PRICING,
        record=record(),
        transport=transport,
        base_url="https://anthropic.test",
    )


def _request() -> ModelRequest:
    return ModelRequest(
        messages=(
            Message(role="system", content="You are ezCHAIN's agent."),
            Message(role="user", content="place a small order"),
        ),
        tools=TOOLS,
    )


def test_a_tool_use_block_becomes_a_tool_call_and_the_tools_are_sent() -> None:
    transport = ScriptedTransport([anthropic_tool_use()])

    turn = _client(transport).complete(_request())

    assert isinstance(turn, ModelTurn)
    assert turn.tool_calls == (
        ToolCall(
            id="toolu_1",
            name="ezchain_place_order",
            arguments={"symbol": "ETH/USDC", "notional": "10"},
        ),
    )
    assert turn.answer is None
    sent = transport.requests[0]
    assert sent.url == "https://anthropic.test/v1/messages"
    assert sent.json["model"] == PINNED_MODEL
    assert sent.json["tools"][0]["input_schema"]["type"] == "object"
    assert sent.headers["x-api-key"]
    assert "authorization" not in sent.headers


def test_the_system_prompt_is_top_level_and_the_answer_contract_rides_along() -> None:
    transport = ScriptedTransport([anthropic_text('{"summary": "nothing to report"}')])

    _client(transport).complete(_request())

    body = transport.requests[0].json
    assert body["system"].startswith("You are ezCHAIN's agent.")
    assert "claims" in body["system"]
    assert [message["role"] for message in body["messages"]] == ["user"]


def test_a_tool_result_goes_back_as_the_providers_own_tool_result_block() -> None:
    transport = ScriptedTransport([anthropic_tool_use(call_id="toolu_9")])
    client = _client(transport)
    first = client.complete(_request())

    transport._script.append(anthropic_text('{"summary": "The order is in."}'))
    request = ModelRequest(
        messages=(
            Message(role="system", content="system"),
            Message(role="user", content="place a small order"),
            Message(role="assistant", content="", tool_calls=first.tool_calls),
            Message(role="tool", content='{"order_id":"o1"}', tool_call_id="toolu_9"),
        ),
        tools=TOOLS,
    )
    client.complete(request)

    messages = transport.requests[1].json["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant", "user"]
    assistant = messages[1]["content"]
    assert assistant[-1] == {
        "type": "tool_use",
        "id": "toolu_9",
        "name": "ezchain_place_order",
        "input": {"symbol": "ETH/USDC", "notional": "10"},
    }
    result = messages[2]["content"]
    assert result == [
        {"type": "tool_result", "tool_use_id": "toolu_9", "content": '{"order_id":"o1"}'}
    ]
    assert "toolu_9" not in str(messages[0])
    assert "tool_result" in str(messages[2])


def test_consecutive_tool_results_are_merged_into_one_user_turn() -> None:
    transport = ScriptedTransport([anthropic_text('{"summary": "done"}')])
    request = ModelRequest(
        messages=(
            Message(role="user", content="go"),
            Message(
                role="assistant",
                content="",
                tool_calls=(
                    ToolCall(id="a", name="ezchain_get_account"),
                    ToolCall(id="b", name="ezchain_get_state"),
                ),
            ),
            Message(role="tool", content="{}", tool_call_id="a"),
            Message(role="tool", content="{}", tool_call_id="b"),
        ),
        tools=TOOLS,
    )

    _client(transport).complete(request)

    messages = transport.requests[0].json["messages"]
    assert len(messages) == 3
    assert [block["tool_use_id"] for block in messages[2]["content"]] == ["a", "b"]


def test_usage_is_counted_and_priced_from_the_operators_rate() -> None:
    transport = ScriptedTransport(
        [
            anthropic_text(
                '{"summary": "done"}', prompt_tokens=1_000_000, completion_tokens=1_000_000
            )
        ]
    )

    turn = _client(transport).complete(_request())

    assert turn.usage.prompt_tokens == 1_000_000
    assert turn.usage.completion_tokens == 1_000_000
    assert turn.usage.cost_usd == Decimal("18.000000")


def test_the_final_text_becomes_the_loops_answer_object() -> None:
    transport = ScriptedTransport(
        [
            anthropic_text(
                '{"summary": "The account was read.", "claims": [{"label": "equity", '
                '"value": "1000.00", "tool": "ezchain_get_account", "path": "equity"}]}'
            )
        ]
    )

    turn = _client(transport).complete(_request())

    assert turn.answer is not None
    assert turn.answer.claims[0].value == Decimal("1000.00")
    assert turn.answer.claims[0].tool == "ezchain_get_account"


def test_prose_that_is_not_the_answer_object_is_left_for_the_loop_to_refuse() -> None:
    transport = ScriptedTransport([anthropic_text("Your equity looks fine to me.")])

    turn = _client(transport).complete(_request())

    assert turn.answer is None
    assert turn.tool_calls == ()
    assert turn.text == "Your equity looks fine to me."


def test_a_response_with_no_content_is_a_protocol_error() -> None:
    transport = ScriptedTransport([HttpResponse(status=200, body={})])

    try:
        _client(transport).complete(_request())
    except Exception as exc:
        assert type(exc).__name__ == "ProviderProtocolError"
    else:
        raise AssertionError("a body with no content should not decode")
