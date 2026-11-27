"""The four harness tools, called through the real MCP server over a fake API.

Every case here goes through ``tools/call``: the schemas the server advertises, the HTTP
path a handler takes and the typed error a refusal produces are all part of the contract.
No case opens a socket or places an order.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from ezchain_mcp.client import EzchainClient
from ezchain_mcp.credentials import Credential
from ezchain_mcp.server import McpServer
from ezchain_mcp.tools import INDICATOR_PERIOD

CHAIN = "solana"
TOKEN = "So11111111111111111111111111111111111111112"
PROFILE = {
    "id": "p1",
    "name": "baseline",
    "chain": None,
    "kind": "manual",
    "stop_loss_pct": "8",
    "trailing_pct": None,
    "take_profit_pct": "20",
    "rules": [],
    "created_at": "2026-11-27T10:00:00+00:00",
    "updated_at": "2026-11-27T10:00:00+00:00",
    "mode": "paper",
}
DECLARATIONS = (
    ("ezchain_market_trend", "read"),
    ("ezchain_chart_spec", "read"),
    ("ezchain_list_protection_profiles", "read"),
    ("ezchain_create_protection_profile", "trade"),
)


def _closes(kind: str = "up") -> list[str]:
    if kind == "down":
        # Fifteen rises then nine sharper falls: RSI has a real downward step at the tail.
        return [str(100 + i) for i in range(15)] + [str(114 - 2 * i) for i in range(9)]
    if kind == "flat":
        return ["100"] * 24
    return [str(100 + i) for i in range(24)]


def _transport(
    calls: list[httpx.Request],
    *,
    closes: list[str] | None = None,
    candle_reason: str | None = None,
    post_status: int = 201,
    post_code: str | None = None,
) -> httpx.MockTransport:
    observed = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path.endswith("/candles"):
            count = int(request.url.params.get("limit", 24))
            prices = (
                []
                if candle_reason
                else (closes if closes is not None else [str(100 + i) for i in range(count)])
            )
            bars = [
                {
                    "open_time": f"2026-11-27T10:{i:02d}:00+00:00",
                    "close": price,
                    "open": price,
                    "high": price,
                    "low": price,
                    "volume": None,
                }
                for i, price in enumerate(prices)
            ]
            return httpx.Response(
                200,
                json={
                    "chain": CHAIN,
                    "token_address": TOKEN,
                    "timeframe": request.url.params.get("timeframe"),
                    "candles": bars,
                    "source": {"name": "geckoterminal", "url": None, "observed_at": observed},
                    "stale": False,
                    "reason": candle_reason,
                },
            )
        if path == "/api/v1/protection/profiles":
            if request.method == "GET":
                return httpx.Response(200, json=[PROFILE])
            if post_code is not None:
                return httpx.Response(
                    post_status,
                    json={"detail": {"code": post_code, "message": "rejected by the API"}},
                )
            return httpx.Response(201, json={**PROFILE, "name": "my profile", "id": "p2"})
        return httpx.Response(404, json={"detail": {"code": "NOT_FOUND", "message": path}})

    return httpx.MockTransport(handler)


@pytest.fixture
def calls() -> list[httpx.Request]:
    return []


def _server(calls: list[httpx.Request], **kwargs: Any) -> McpServer:
    client = EzchainClient("http://test", "token", transport=_transport(calls, **kwargs))
    return McpServer(client, allow_writes=True, credential=Credential("scoped"))


def _call(
    server: McpServer, name: str, arguments: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    assert response is not None
    result = response["result"]
    body = json.loads(result["content"][0]["text"])
    return result, body


def _listed(server: McpServer) -> dict[str, dict[str, Any]]:
    response: Any = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    return {tool["name"]: tool for tool in response["result"]["tools"]}


# -- declarations ----------------------------------------------------------


@pytest.mark.parametrize(("name", "scope"), DECLARATIONS)
def test_each_tool_declares_input_output_scope_and_trace(
    calls: list[httpx.Request], name: str, scope: str
) -> None:
    tool = _listed(_server(calls))[name]

    assert tool["scope"] == scope
    assert tool["traceEvent"] == "tool_call"
    assert tool["inputSchema"]["type"] == "object"
    assert tool["inputSchema"]["additionalProperties"] is False
    assert tool["outputSchema"]
    assert tool["annotations"]["readOnlyHint"] is (scope == "read")


def test_the_three_read_tools_are_visible_without_a_write_credential(
    calls: list[httpx.Request],
) -> None:
    client = EzchainClient("http://test", transport=_transport(calls))
    names = set(_listed(McpServer(client)))

    assert {name for name, scope in DECLARATIONS if scope == "read"} <= names
    assert "ezchain_create_protection_profile" not in names


# -- market_trend ----------------------------------------------------------


def test_market_trend_sma_returns_points_latest_direction_and_source(
    calls: list[httpx.Request],
) -> None:
    result, body = _call(
        _server(calls),
        "ezchain_market_trend",
        {"chain": CHAIN, "token": TOKEN, "window": "1d", "series": "sma"},
    )

    assert result["isError"] is False
    assert body["series"] == "sma"
    assert body["period"] == INDICATOR_PERIOD
    assert len(body["points"]) == 24 - INDICATOR_PERIOD + 1
    assert body["latest"] == pytest.approx((110 + 123) / 2)
    assert body["direction"] == "up"
    assert body["source"]["name"] == "geckoterminal"
    assert body["age_seconds"] is not None and body["age_seconds"] >= 60
    assert body["stale"] is False
    assert "floats" in body["arithmetic"]
    assert isinstance(result["structuredContent"]["latest"], float)


def test_market_trend_ema_is_a_floats_only_indicator_result(calls: list[httpx.Request]) -> None:
    _body_result, body = _call(
        _server(calls),
        "ezchain_market_trend",
        {"chain": CHAIN, "token": TOKEN, "window": "4h", "series": "ema"},
    )

    assert body["direction"] == "up"
    assert body["latest"] > body["points"][0]["value"]
    assert all(isinstance(point["value"], float) for point in body["points"])
    assert all(isinstance(point["open_time"], str) for point in body["points"])


def test_market_trend_rsi_reports_a_down_turn(calls: list[httpx.Request]) -> None:
    _result, body = _call(
        _server(calls, closes=_closes("down")),
        "ezchain_market_trend",
        {"chain": CHAIN, "token": TOKEN, "window": "1d", "series": "rsi"},
    )

    assert body["direction"] == "down"
    assert body["latest"] < body["points"][-2]["value"]
    assert all(0.0 <= point["value"] <= 100.0 for point in body["points"])


def test_market_trend_rsi_flat_window_is_neutral(calls: list[httpx.Request]) -> None:
    _result, body = _call(
        _server(calls, closes=_closes("flat")),
        "ezchain_market_trend",
        {"chain": CHAIN, "token": TOKEN, "window": "1d", "series": "rsi"},
    )

    assert body["direction"] == "flat"
    assert body["latest"] == 50.0


@pytest.mark.parametrize(
    ("window", "timeframe", "limit"),
    [("1h", "1m", 60), ("4h", "5m", 48), ("1d", "1h", 24), ("7d", "4h", 42)],
)
def test_each_window_maps_to_one_stored_candle_timeframe(
    calls: list[httpx.Request], window: str, timeframe: str, limit: int
) -> None:
    _result, body = _call(
        _server(calls),
        "ezchain_market_trend",
        {"chain": CHAIN, "token": TOKEN, "window": window, "series": "sma"},
    )

    assert body["window"] == window
    assert body["timeframe"] == timeframe
    assert calls[-1].url.params["timeframe"] == timeframe
    assert calls[-1].url.params["limit"] == str(limit)


@pytest.mark.parametrize("window", ["2h", "1w", ""])
def test_an_unknown_window_is_refused_before_any_http_call(
    calls: list[httpx.Request], window: str
) -> None:
    result, body = _call(
        _server(calls),
        "ezchain_market_trend",
        {"chain": CHAIN, "token": TOKEN, "window": window, "series": "sma"},
    )

    assert result["isError"] is True
    assert body["code"] == "INVALID_WINDOW"
    assert calls == []


def test_an_unknown_series_is_refused(calls: list[httpx.Request]) -> None:
    result, body = _call(
        _server(calls),
        "ezchain_market_trend",
        {"chain": CHAIN, "token": TOKEN, "window": "1d", "series": "bollinger"},
    )

    assert result["isError"] is True
    assert body["code"] == "INVALID_SERIES"
    assert calls == []


def test_an_unknown_token_has_no_series_to_compute(calls: list[httpx.Request]) -> None:
    result, body = _call(
        _server(calls, candle_reason="NO_POOL"),
        "ezchain_market_trend",
        {"chain": CHAIN, "token": "unknown", "window": "1d", "series": "sma"},
    )

    assert result["isError"] is True
    assert body["code"] == "UNKNOWN_TOKEN"
    assert len(calls) == 1


# -- chart_spec ------------------------------------------------------------


def test_chart_spec_is_a_declarative_spec_with_aligned_overlays(
    calls: list[httpx.Request],
) -> None:
    result, body = _call(
        _server(calls),
        "ezchain_chart_spec",
        {"chain": CHAIN, "token": TOKEN, "window": "1d", "overlays": ["sma", "ema"]},
    )

    assert result["isError"] is False
    assert body["spec_version"] == 1
    assert body["axes"]["x"]["type"] == "time"
    assert body["axes"]["y"]["field"] == "value"
    close = body["series"][0]
    assert close["name"] == "close"
    assert all(isinstance(point["value"], str) for point in close["values"])
    assert [overlay["name"] for overlay in body["overlays"]] == ["sma", "ema"]
    overlay = body["overlays"][0]
    assert overlay["period"] == INDICATOR_PERIOD
    assert all(isinstance(point["value"], float) for point in overlay["values"])
    assert overlay["values"][-1]["open_time"] == close["values"][-1]["open_time"]
    assert body["source"]["name"] == "geckoterminal"
    assert body["age_seconds"] is not None and body["age_seconds"] >= 60
    assert body["stale"] is False


def test_chart_spec_without_overlays_still_returns_close_and_freshness(
    calls: list[httpx.Request],
) -> None:
    _result, body = _call(
        _server(calls),
        "ezchain_chart_spec",
        {"chain": CHAIN, "token": TOKEN, "window": "1h"},
    )

    assert body["overlays"] == []
    assert len(body["series"][0]["values"]) == 60
    assert body["source"] is not None


def test_an_unknown_overlay_is_refused(calls: list[httpx.Request]) -> None:
    result, body = _call(
        _server(calls),
        "ezchain_chart_spec",
        {"chain": CHAIN, "token": TOKEN, "window": "1d", "overlays": ["rsi"]},
    )

    assert result["isError"] is True
    assert body["code"] == "INVALID_OVERLAY"
    assert calls == []


# -- protection profiles ---------------------------------------------------


def test_list_protection_profiles_reads_the_existing_api_route(
    calls: list[httpx.Request],
) -> None:
    result, body = _call(_server(calls), "ezchain_list_protection_profiles", {})

    assert result["isError"] is False
    assert body == {"result": [PROFILE]}
    assert calls[-1].method == "GET"
    assert calls[-1].url.path == "/api/v1/protection/profiles"


def test_create_protection_profile_posts_exact_decimals_through_the_api(
    calls: list[httpx.Request],
) -> None:
    result, body = _call(
        _server(calls),
        "ezchain_create_protection_profile",
        {"name": "my profile", "stop_loss_pct": 8, "take_profit_pct": "20"},
    )

    assert result["isError"] is False
    assert body["persisted"] is True
    assert body["profile"]["id"] == "p2"
    request = calls[-1]
    assert request.method == "POST"
    assert request.url.path == "/api/v1/protection/profiles"
    assert json.loads(request.content) == {
        "name": "my profile",
        "chain": None,
        "kind": "manual",
        "params": {"stop_loss_pct": "8", "take_profit_pct": "20"},
        "rules": [],
    }
    assert not any("orders" in call.url.path or "arms" in call.url.path for call in calls)


def test_create_protection_profile_never_rounds_a_percentage(
    calls: list[httpx.Request],
) -> None:
    precise = "0.1234567890123456789012345"
    _result, body = _call(
        _server(calls),
        "ezchain_create_protection_profile",
        {"name": "precise", "trailing_pct": precise},
    )

    assert body["request"]["params"]["trailing_pct"] == precise
    assert json.loads(calls[-1].content)["params"]["trailing_pct"] == precise


def test_a_dry_run_validates_and_previews_without_persisting(
    calls: list[httpx.Request],
) -> None:
    _result, body = _call(
        _server(calls),
        "ezchain_create_protection_profile",
        {"name": "preview", "stop_loss_pct": 7.5, "dry_run": True},
    )

    assert body["dry_run"] is True
    assert body["persisted"] is False
    assert body["profile"] is None
    assert body["request"]["params"] == {"stop_loss_pct": "7.5"}
    assert calls == []


@pytest.mark.parametrize("percentage", ["-5", "abc", float("nan"), True, 0])
def test_an_invalid_percentage_is_refused_without_an_http_call(
    calls: list[httpx.Request], percentage: Any
) -> None:
    result, body = _call(
        _server(calls),
        "ezchain_create_protection_profile",
        {"name": "bad", "stop_loss_pct": percentage},
    )

    assert result["isError"] is True
    assert body["code"] == "INVALID_PERCENTAGE"
    assert calls == []


def test_a_profile_needs_at_least_one_percentage(calls: list[httpx.Request]) -> None:
    result, body = _call(
        _server(calls), "ezchain_create_protection_profile", {"name": "empty"}
    )

    assert result["isError"] is True
    assert body["code"] == "INVALID_PERCENTAGE"
    assert calls == []


def test_an_empty_name_is_refused(calls: list[httpx.Request]) -> None:
    result, body = _call(
        _server(calls),
        "ezchain_create_protection_profile",
        {"name": "  ", "stop_loss_pct": 5},
    )

    assert result["isError"] is True
    assert body["code"] == "INVALID_NAME"
    assert calls == []


def test_the_api_remains_the_authority_on_profile_ranges(calls: list[httpx.Request]) -> None:
    result, body = _call(
        _server(calls, post_status=422, post_code="PROTECTION_PARAM_RANGE"),
        "ezchain_create_protection_profile",
        {"name": "wide", "stop_loss_pct": 500},
    )

    assert result["isError"] is True
    assert body["code"] == "PROTECTION_PARAM_RANGE"
    assert body["status"] == 422


def test_a_read_only_server_refuses_the_creation_tool(calls: list[httpx.Request]) -> None:
    client = EzchainClient("http://test", transport=_transport(calls))
    result, body = _call(McpServer(client), "ezchain_create_protection_profile", {})

    assert result["isError"] is True
    assert body["code"] == "WRITES_DISABLED"
    assert calls == []
