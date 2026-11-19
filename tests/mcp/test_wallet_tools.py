"""Wallet MCP tools declare their cost and effect and call the wallet API."""

from __future__ import annotations

import httpx

from ezchain_mcp.client import EzchainClient
from ezchain_mcp.credentials import Credential
from ezchain_mcp.server import McpServer
from ezchain_mcp.tools import all_tools, tool_by_name


def _client(calls: list[httpx.Request]) -> EzchainClient:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.url.path == "/api/v1/wallets/track":
            return httpx.Response(201, json={"id": "w1"})
        return httpx.Response(200, json=[{"id": "w1"}])

    return EzchainClient("http://test", transport=httpx.MockTransport(handler))


def test_every_wallet_tool_declares_cost_and_effect() -> None:
    tools = tool_by_name(True)
    wallet_tools = [
        tool
        for tool in tools.values()
        if tool.name.startswith("ezchain_") and "wallet" in tool.name
    ]
    assert wallet_tools
    assert all(tool.cost in ("free", "paid") for tool in wallet_tools)
    assert all(tool.effect in ("read", "configure", "trade") for tool in wallet_tools)
    assert all("cost" in tool.to_dict() and "effect" in tool.to_dict() for tool in wallet_tools)


def test_wallet_handlers_target_the_wallet_routes() -> None:
    calls: list[httpx.Request] = []
    client = _client(calls)
    tools = tool_by_name(True)
    tools["ezchain_track_wallet"].handler(
        client, {"chain": "solana", "address": "PLACEHOLDER", "label": "Alpha"}
    )
    tools["ezchain_get_wallet"].handler(client, {"wallet_id": "w 1"})
    tools["ezchain_untrack_wallet"].handler(client, {"wallet_id": "w1"})
    assert calls[0].url.path == "/api/v1/wallets/track"
    assert calls[1].url.raw_path == b"/api/v1/wallets/tracked/w%201"
    assert calls[2].method == "DELETE"


def test_read_only_server_hides_wallet_configuration() -> None:
    server = McpServer(_client([]))
    listing = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert listing is not None
    names = {tool["name"] for tool in listing["result"]["tools"]}
    assert "ezchain_get_wallet" in names
    assert "ezchain_track_wallet" not in names
    assert len(all_tools(True)) > len(all_tools(False))


def test_writes_need_a_verified_scoped_credential() -> None:
    server = McpServer(_client([]), allow_writes=True, credential=Credential("admin", "admin"))
    assert server.allow_writes is False
