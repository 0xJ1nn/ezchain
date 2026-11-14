"""Criterion (c): a provider error or timeout ends the turn and never repeats a tool call.

The failure this pins is a session that re-asks the model after a transport error: the model
still sees the transcript up to its own tool call, so it asks for the order again and a paper
order becomes two. The client therefore makes exactly one transport call and raises; the loop
turns that raise into a failed session, keeping the tool calls that already executed.

These tests run the real client inside the real loop with a fake MCP server, so the number of
executed orders is ``FakeMcp.calls`` — an observation, not a claim about one.
"""

from __future__ import annotations

import httpx
import pytest
from providers_helpers import (
    PINNED_MODEL,
    PLACE_ORDER,
    PRICING,
    FakeMcp,
    FixedClock,
    ScriptedTransport,
    anthropic_text,
    anthropic_tool_use,
    make_limits,
    record,
)

from ezchain_agent_runtime import (
    AgentLoop,
    Message,
    ModelRequest,
    SessionState,
    StopReason,
    ToolResult,
)
from ezchain_harness import (
    AnthropicClient,
    HttpResponse,
    ProviderTimeout,
    ProviderTransportError,
)

ORDER_ACCEPTED = ToolResult(
    tool=PLACE_ORDER.name,
    data={"order_id": "o-1", "status": "accepted", "as_of": "2026-11-14T09:00:00Z"},
)


def _loop(transport: ScriptedTransport, mcp: FakeMcp) -> AgentLoop:
    client = AnthropicClient(
        model_id=PINNED_MODEL,
        pricing=PRICING,
        record=record(),
        transport=transport,
        clock=FixedClock(),
    )
    return AgentLoop(model=client, toolbox=mcp, limits=make_limits(), clock=FixedClock())


def test_a_provider_failure_after_a_money_moving_call_does_not_repeat_it() -> None:
    transport = ScriptedTransport(
        [
            anthropic_tool_use(call_id="toolu_order", name=PLACE_ORDER.name),
            ProviderTransportError("connection reset by peer"),
        ]
    )
    mcp = FakeMcp({PLACE_ORDER.name: ORDER_ACCEPTED})

    outcome = _loop(transport, mcp).run("buy a little ETH")

    assert [name for name, _ in mcp.calls] == [PLACE_ORDER.name]
    assert len(mcp.calls) == 1, "the order must have been placed exactly once"
    assert transport.calls == 2, "one turn per model call; the failed turn is not retried"
    assert outcome.state is SessionState.FAILED
    assert outcome.reason is StopReason.MODEL_FAILED
    assert "connection reset" in (outcome.detail or "")
    assert outcome.usage.turns == 1


def test_a_timeout_ends_the_session_without_a_second_attempt() -> None:
    transport = ScriptedTransport(
        [
            anthropic_tool_use(call_id="toolu_order", name=PLACE_ORDER.name),
            ProviderTimeout("api.anthropic.com did not answer within 60s"),
        ]
    )
    mcp = FakeMcp({PLACE_ORDER.name: ORDER_ACCEPTED})

    outcome = _loop(transport, mcp).run("buy a little ETH")

    assert len(mcp.calls) == 1
    assert transport.calls == 2
    assert outcome.state is SessionState.FAILED
    assert outcome.reason is StopReason.MODEL_FAILED
    assert outcome.answer is None


def test_a_real_httpx_timeout_inside_the_loop_is_one_failed_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def explode(*args: object, **kwargs: object) -> object:
        calls.append(1)
        raise httpx.ReadTimeout("too slow")

    monkeypatch.setattr(httpx, "request", explode)
    mcp = FakeMcp({PLACE_ORDER.name: ORDER_ACCEPTED})
    session = record()
    client = AnthropicClient(
        model_id=PINNED_MODEL,
        pricing=PRICING,
        record=session,
        clock=FixedClock(),
    )

    outcome = AgentLoop(model=client, toolbox=mcp, limits=make_limits(), clock=FixedClock()).run(
        "buy a little ETH"
    )

    assert calls == [1], "a real timeout must not be retried"
    assert mcp.calls == []
    assert outcome.state is SessionState.FAILED
    assert outcome.reason is StopReason.MODEL_FAILED
    assert "did not answer within" in (outcome.detail or "")
    assert session.turns == ()


def test_a_provider_http_error_after_a_tool_call_does_not_replay_the_turn() -> None:
    transport = ScriptedTransport(
        [
            anthropic_tool_use(call_id="toolu_order", name=PLACE_ORDER.name),
            HttpResponse(
                status=529,
                body={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}},
            ),
        ]
    )
    mcp = FakeMcp({PLACE_ORDER.name: ORDER_ACCEPTED})

    outcome = _loop(transport, mcp).run("buy a little ETH")

    assert len(mcp.calls) == 1
    assert transport.calls == 2
    assert outcome.state is SessionState.FAILED
    assert outcome.reason is StopReason.MODEL_FAILED
    assert "529" in (outcome.detail or "")
    assert "busy" in (outcome.detail or "")


def test_the_client_makes_exactly_one_transport_call_per_complete() -> None:
    transport = ScriptedTransport([ProviderTransportError("socket closed")])
    client = AnthropicClient(
        model_id=PINNED_MODEL,
        pricing=PRICING,
        record=record(),
        transport=transport,
    )

    with pytest.raises(ProviderTransportError):
        client.complete(ModelRequest(messages=(Message(role="user", content="hi"),), tools=()))

    assert transport.calls == 1


def test_a_failure_before_any_tool_call_still_ends_the_session_once() -> None:
    transport = ScriptedTransport([ProviderTransportError("dns failure")])
    mcp = FakeMcp({})

    outcome = _loop(transport, mcp).run("do nothing")

    assert mcp.calls == []
    assert transport.calls == 1
    assert outcome.state is SessionState.FAILED
    assert outcome.reason is StopReason.MODEL_FAILED


def test_a_successful_round_trip_still_completes_after_the_money_call() -> None:
    transport = ScriptedTransport(
        [
            anthropic_tool_use(call_id="toolu_order", name=PLACE_ORDER.name),
            anthropic_text('{"summary": "The paper order was accepted."}'),
        ]
    )
    mcp = FakeMcp({PLACE_ORDER.name: ORDER_ACCEPTED})

    outcome = _loop(transport, mcp).run("buy a little ETH")

    assert len(mcp.calls) == 1
    assert outcome.state is SessionState.DONE
    assert outcome.reason is StopReason.COMPLETED
