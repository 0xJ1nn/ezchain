"""MCP protocol, tool filtering and resource mapping against a fake API."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from ezchain_mcp.client import EzchainClient
from ezchain_mcp.server import PROTOCOL_VERSION, McpServer


def _transport(calls: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/api/v1/agent/capabilities":
            return httpx.Response(200, json={"mode": {"execution": "paper"}})
        if path == "/api/v1/markets":
            return httpx.Response(200, json={"rows": [{"symbol": "SOL"}], "stale": False})
        if path == "/api/v1/strategies":
            return httpx.Response(200, json=[{"id": "s1", "name": "one"}])
        if path == "/api/v1/strategies/s1/runs":
            return httpx.Response(200, json=[{"id": "r1", "action": "hold"}])
        if path == "/api/v1/paper/orders":
            return httpx.Response(201, json={"id": "o1", "status": "filled"})
        if path == "/api/v1/paper/positions":
            return httpx.Response(200, json=[])
        if path == "/api/v1/system/kill-switch":
            return httpx.Response(200, json={"active": False})
        if path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/readyz":
            return httpx.Response(200, json={"status": "ready"})
        return httpx.Response(404, json={"detail": {"code": "NOT_FOUND", "message": path}})

    return httpx.MockTransport(handler)


@pytest.fixture
def calls() -> list[httpx.Request]:
    return []


@pytest.fixture
def client(calls: list[httpx.Request]) -> EzchainClient:
    return EzchainClient("http://test", "token", transport=_transport(calls))


def test_initialize_negotiates_the_protocol(client: EzchainClient) -> None:
    server = McpServer(client)
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}},
        }
    )
    assert response is not None
    assert response["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert response["result"]["capabilities"]["tools"]["listChanged"] is False
    assert "PAPER" in response["result"]["instructions"]


def test_read_only_hides_every_write_tool(client: EzchainClient) -> None:
    server = McpServer(client)
    response = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert response is not None
    names = {tool["name"] for tool in response["result"]["tools"]}
    assert "ezchain_get_account" in names
    assert "ezchain_place_paper_order" not in names

    allowed = McpServer(client, allow_writes=True).handle(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}
    )
    assert allowed is not None
    names = {tool["name"] for tool in allowed["result"]["tools"]}
    assert "ezchain_place_paper_order" in names
    assert "ezchain_arm_kill_switch" in names


def test_read_tool_returns_structured_content(
    client: EzchainClient, calls: list[httpx.Request]
) -> None:
    server = McpServer(client)
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "ezchain_list_markets", "arguments": {"limit": 5}},
        }
    )
    assert response is not None
    result = response["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["rows"][0]["symbol"] == "SOL"
    assert calls[-1].url.params["limit"] == "5"


def test_write_tool_is_unknown_in_read_only_mode(client: EzchainClient) -> None:
    server = McpServer(client)
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "ezchain_place_paper_order", "arguments": {}},
        }
    )
    assert response is not None
    assert response["error"]["code"] == -32602


def test_paper_order_tool_uses_the_paper_endpoint(
    client: EzchainClient, calls: list[httpx.Request]
) -> None:
    server = McpServer(client, allow_writes=True)
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "ezchain_place_paper_order",
                "arguments": {
                    "chain": "solana",
                    "token_address": "So11111111111111111111111111111111111111112",
                    "side": "buy",
                    "amount_usd": "10",
                    "idempotency_key": "test-key-1",
                },
            },
        }
    )
    assert response is not None
    assert response["result"]["isError"] is False
    assert calls[-1].url.path == "/api/v1/paper/orders"
    body = json.loads(calls[-1].content)
    assert "mode" not in body


def test_api_error_becomes_a_tool_error(client: EzchainClient) -> None:
    server = McpServer(client)
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "ezchain_get_strategy", "arguments": {"strategy_id": "missing"}},
        }
    )
    assert response is not None
    assert response["result"]["isError"] is True
    body = json.loads(response["result"]["content"][0]["text"])
    assert body["code"] == "NOT_FOUND"


def test_resources_list_and_read(client: EzchainClient) -> None:
    server = McpServer(client)
    listed = server.handle({"jsonrpc": "2.0", "id": 8, "method": "resources/list"})
    assert listed is not None
    uris = {item["uri"] for item in listed["result"]["resources"]}
    assert "ezchain://state" in uris
    assert "ezchain://strategies/s1" in uris

    read = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "resources/read",
            "params": {"uri": "ezchain://strategies/s1/runs"},
        }
    )
    assert read is not None
    body = json.loads(read["result"]["contents"][0]["text"])
    assert body == [{"id": "r1", "action": "hold"}]


def test_unknown_method_and_notification(client: EzchainClient) -> None:
    server = McpServer(client)
    unknown = server.handle({"jsonrpc": "2.0", "id": 10, "method": "nope"})
    assert unknown is not None
    assert unknown["error"]["code"] == -32601
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tool_schemas_are_closed_objects(client: EzchainClient) -> None:
    server = McpServer(client, allow_writes=True)
    response: Any = server.handle({"jsonrpc": "2.0", "id": 11, "method": "tools/list"})
    for tool in response["result"]["tools"]:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["inputSchema"]["additionalProperties"] is False
