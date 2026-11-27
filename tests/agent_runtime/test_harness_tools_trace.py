"""The four harness tools are traced as standard tool-call events, with inputs digested.

The loop already writes one ``tool_call`` event per invocation. These cases drive the real
MCP server through that loop so the link between a declared trace event, the arguments an
agent sent and the append-only event is pinned rather than assumed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from agent_helpers import FakeModel, FixedClock, make_limits
from injection_harness import McpServerToolbox

from ezchain_agent_runtime import (
    AgentLoop,
    Answer,
    ModelTurn,
    ToolCall,
    digest_json,
)
from ezchain_agent_runtime.trace import MemoryTraceLog, TraceEventKind
from ezchain_mcp.client import EzchainClient
from ezchain_mcp.credentials import Credential
from ezchain_mcp.server import McpServer

CHAIN = "solana"
TOKEN = "So11111111111111111111111111111111111111112"
SECRET = "ezk_this_is_not_an_agent_credential"


def _transport(calls: list[httpx.Request]) -> httpx.MockTransport:
    observed = datetime.now(UTC).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path.endswith("/candles"):
            return httpx.Response(
                200,
                json={
                    "chain": CHAIN,
                    "token_address": TOKEN,
                    "timeframe": request.url.params.get("timeframe"),
                    "candles": [
                        {
                            "open_time": f"2026-11-27T10:{i:02d}:00+00:00",
                            "close": str(100 + i),
                            "open": "100",
                            "high": "100",
                            "low": "100",
                            "volume": None,
                        }
                        for i in range(24)
                    ],
                    "source": {"name": "geckoterminal", "url": None, "observed_at": observed},
                    "stale": False,
                    "reason": None,
                },
            )
        if path == "/api/v1/protection/profiles" and request.method == "GET":
            return httpx.Response(200, json=[{"id": "p1", "name": "baseline"}])
        return httpx.Response(404, json={"detail": {"code": "NOT_FOUND", "message": path}})

    return httpx.MockTransport(handler)


@pytest.fixture
def calls() -> list[httpx.Request]:
    return []


def _loop(calls: list[httpx.Request], model: FakeModel, trace: MemoryTraceLog) -> Any:
    server = McpServer(
        EzchainClient("http://test", SECRET, transport=_transport(calls)),
        allow_writes=True,
        credential=Credential("scoped"),
    )
    return AgentLoop(
        model=model,
        toolbox=McpServerToolbox(server),
        limits=make_limits(session_turns=8),
        clock=FixedClock(),
        trace=trace,
    )


CALLS = (
    (
        "ezchain_market_trend",
        {"chain": CHAIN, "token": TOKEN, "window": "1d", "series": "sma"},
    ),
    (
        "ezchain_chart_spec",
        {"chain": CHAIN, "token": TOKEN, "window": "1d", "overlays": ["sma"]},
    ),
    ("ezchain_list_protection_profiles", {}),
    (
        "ezchain_create_protection_profile",
        {"name": "traced", "stop_loss_pct": "8", "dry_run": True},
    ),
)


def test_every_harness_tool_call_writes_one_traced_event_with_its_inputs(
    calls: list[httpx.Request],
) -> None:
    trace = MemoryTraceLog()
    model = FakeModel(
        [
            *(
                ModelTurn(tool_calls=(ToolCall(id=f"c{i}", name=name, arguments=args),))
                for i, (name, args) in enumerate(CALLS)
            ),
            ModelTurn(answer=Answer(summary="four tools were traced")),
        ]
    )

    outcome = _loop(calls, model, trace).run("analyse the market and preview a profile")

    events = [
        event for event in trace.read(outcome.session_id) if event.kind is TraceEventKind.TOOL_CALL
    ]
    assert [event.fields["tool"] for event in events] == [name for name, _ in CALLS]
    for event, (_name, args) in zip(events, CALLS, strict=True):
        assert event.kind.value == "tool_call"
        assert event.fields["arguments_digest"] == digest_json(args)
        assert event.fields["status"] == "ok"
        assert SECRET not in str(event.fields)
    assert not any("orders" in call.url.path for call in calls)


def test_a_refused_tool_call_is_traced_with_its_arguments_and_error_code(
    calls: list[httpx.Request],
) -> None:
    trace = MemoryTraceLog()
    args = {"chain": CHAIN, "token": TOKEN, "window": "2h", "series": "sma"}
    model = FakeModel(
        [
            ModelTurn(tool_calls=(ToolCall(id="c1", name="ezchain_market_trend", arguments=args),)),
            ModelTurn(answer=Answer(summary="the window was refused")),
        ]
    )

    outcome = _loop(calls, model, trace).run("try a bad window")

    events = [
        event for event in trace.read(outcome.session_id) if event.kind is TraceEventKind.TOOL_CALL
    ]
    assert len(events) == 1
    assert events[0].fields["status"] == "error"
    assert events[0].fields["code"] == "INVALID_WINDOW"
    assert events[0].fields["arguments_digest"] == digest_json(args)
    assert SECRET not in str(events[0].fields)
    assert calls == []
