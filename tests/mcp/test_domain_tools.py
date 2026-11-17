"""Every declared tool has a contract and an injection test, and domains register by module.

The new domains — watchlist, protection, alerts, auto-buys and search/quotes — are declared in
their own ``tools_*.py`` modules. This suite walks the assembled surface, so a tool added
anywhere is checked without editing a central list.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from ezchain_mcp.client import EzchainClient
from ezchain_mcp.credentials import Credential
from ezchain_mcp.registry import all_tools
from ezchain_mcp.server import McpServer

FORGED_FIELDS = ("mode", "live", "execution", "approved", "executed", "approval_status")
FORBIDDEN_HINTS = (
    "shell",
    "read_file",
    "write_file",
    "fetch_url",
    "admin",
    "kill_switch",
    "secret",
    "credential",
)


def _transport(calls: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/api/v1/agent/capabilities":
            return httpx.Response(200, json={"mode": {"execution": "paper"}})
        return httpx.Response(200, json={"ok": True})

    return httpx.MockTransport(handler)


@pytest.fixture
def calls() -> list[httpx.Request]:
    return []


@pytest.fixture
def server(calls: list[httpx.Request]) -> McpServer:
    client = EzchainClient("http://test", "token", transport=_transport(calls))
    return McpServer(client, allow_writes=True, credential=Credential("scoped"))


def _call(server: McpServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    assert response is not None
    assert response["result"]["isError"] is False
    return json.loads(response["result"]["content"][0]["text"])


# --- contract tests: one row per tool --------------------------------------------------


@pytest.mark.parametrize("tool", all_tools(allow_writes=True), ids=lambda tool: tool.name)
def test_every_tool_is_a_closed_object_with_a_valid_declaration(tool: Any) -> None:
    schema = tool.input_schema
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert tool.cost in ("free", "paid")
    assert tool.effect in ("read", "configure", "trade")
    assert tool.read_only == (tool.effect == "read")


@pytest.mark.parametrize("name", ["ezchain_create_auto_buy", "ezchain_resume_auto_buy"])
def test_a_recurring_buy_declares_a_trade_effect(name: str) -> None:
    tool = next(tool for tool in all_tools(allow_writes=True) if tool.name == name)
    assert tool.effect == "trade"
    assert tool.read_only is False


# --- injection tests: one row per tool -------------------------------------------------


@pytest.mark.parametrize("tool", all_tools(allow_writes=True), ids=lambda tool: tool.name)
def test_no_tool_advertises_an_injectable_or_local_capability(tool: Any) -> None:
    properties = set(tool.input_schema.get("properties", {}))
    assert properties.isdisjoint(FORGED_FIELDS), tool.name
    for hint in FORBIDDEN_HINTS:
        assert hint not in tool.name, (tool.name, hint)


# The order tool deliberately forwards its body so the API's closed schema can refuse a
# forged field with a 422; that loud refusal is what the runtime's injection suite pins.
FORWARDS_FOR_REFUSAL = frozenset({"ezchain_place_paper_order"})


@pytest.mark.parametrize("tool", all_tools(allow_writes=True), ids=lambda tool: tool.name)
def test_a_forged_argument_is_never_silently_accepted(
    tool: Any, calls: list[httpx.Request]
) -> None:
    """A forged field is either dropped before forwarding or left for the API to refuse."""
    client = EzchainClient("http://test", "token", transport=_transport(calls))
    server = McpServer(client, allow_writes=True, credential=Credential("scoped"))
    arguments = {name: "x" for name in tool.input_schema.get("properties", {})}
    arguments.update({field: "forged" for field in FORGED_FIELDS})
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool.name, "arguments": arguments},
        }
    )
    assert response is not None
    if tool.name in FORWARDS_FOR_REFUSAL:
        # The mock answers 200, but the field must reach the API for the real one to refuse it.
        text = calls[-1].content.decode()
        assert "approved" in json.loads(text)
        return
    if calls:
        text = calls[-1].content.decode() if calls[-1].content else ""
        forwarded = json.loads(text) if text else {}
        for field in FORGED_FIELDS:
            assert field not in forwarded, (tool.name, field)


# --- the registered domains ------------------------------------------------------------


def test_all_new_domains_are_registered() -> None:
    names = {tool.name for tool in all_tools(allow_writes=True)}
    assert {
        "ezchain_list_watchlist",
        "ezchain_add_to_watchlist",
        "ezchain_remove_from_watchlist",
        "ezchain_list_protection_arms",
        "ezchain_set_stop_loss",
        "ezchain_set_take_profit",
        "ezchain_set_trailing_stop",
        "ezchain_cancel_protection_arm",
        "ezchain_list_exit_plans",
        "ezchain_list_alerts",
        "ezchain_create_auto_buy",
        "ezchain_pause_auto_buy",
        "ezchain_resume_auto_buy",
        "ezchain_search_markets",
        "ezchain_get_quote",
    } <= names


def test_watchlist_tools_use_the_watchlist_routes(
    server: McpServer, calls: list[httpx.Request]
) -> None:
    _call(server, "ezchain_list_watchlist", {})
    assert calls[-1].url.path == "/api/v1/watchlist"

    _call(server, "ezchain_add_to_watchlist", {"chain": "blast", "base_token": "0xabc"})
    assert calls[-1].method == "POST"
    assert calls[-1].url.path == "/api/v1/watchlist"

    _call(server, "ezchain_remove_from_watchlist", {"chain": "blast", "base_token": "0xabc"})
    assert calls[-1].method == "DELETE"


def test_protection_tools_use_the_protection_routes(
    server: McpServer, calls: list[httpx.Request]
) -> None:
    _call(server, "ezchain_list_protection_arms", {})
    assert calls[-1].url.path == "/api/v1/protection/arms"

    for name in ("ezchain_set_stop_loss", "ezchain_set_take_profit", "ezchain_set_trailing_stop"):
        _call(
            server,
            name,
            {
                "chain": "blast",
                "token_address": "0xabcdef",
                "token_symbol": "TEST",
                "params": {"trigger_pct": 5},
            },
        )
        assert calls[-1].method == "POST"
        assert calls[-1].url.path == "/api/v1/protection/arms"
        kind = json.loads(calls[-1].content)["kind"]
        assert (
            kind
            == {
                "ezchain_set_stop_loss": "stop_loss",
                "ezchain_set_take_profit": "take_profit",
                "ezchain_set_trailing_stop": "trailing_stop",
            }[name]
        )

    _call(server, "ezchain_cancel_protection_arm", {"arm_id": "a1"})
    assert calls[-1].url.path == "/api/v1/protection/arms/a1/cancel"


def test_alerts_auto_buys_search_and_quotes_hit_their_routes(
    server: McpServer, calls: list[httpx.Request]
) -> None:
    _call(server, "ezchain_list_alerts", {})
    assert calls[-1].url.path == "/api/v1/account/alerts"

    _call(
        server,
        "ezchain_create_auto_buy",
        {"name": "daily", "chain": "blast", "token_address": "0xabc", "amount_usd": "10"},
    )
    assert calls[-1].method == "POST"
    assert calls[-1].url.path == "/api/v1/strategies"
    assert json.loads(calls[-1].content)["kind"] == "dca"

    _call(server, "ezchain_pause_auto_buy", {"strategy_id": "s1"})
    assert calls[-1].method == "PATCH"
    assert json.loads(calls[-1].content) == {"status": "paused"}

    _call(server, "ezchain_resume_auto_buy", {"strategy_id": "s1"})
    assert json.loads(calls[-1].content) == {"status": "active"}

    _call(server, "ezchain_search_markets", {"q": "sol", "limit": 5})
    assert calls[-1].url.path == "/api/v1/markets"
    assert calls[-1].url.params["q"] == "sol"

    _call(
        server,
        "ezchain_get_quote",
        {"chain": "blast", "token": "0xabc", "side": "buy", "amount_usd": "10"},
    )
    assert calls[-1].url.path == "/api/v1/quotes"
