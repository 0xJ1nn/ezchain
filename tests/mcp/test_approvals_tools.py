"""The MCP order tool reports the approval, and the queue is readable over MCP.

An agent must never read ``accepted`` as ``filled``: under the manual tier the order tool
returns the approval id and status, and the approval list exposes the queue. No MCP tool
locks the automation tier or decides an approval — those are a person's acts.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from ezchain_mcp.client import EzchainClient
from ezchain_mcp.credentials import Credential
from ezchain_mcp.server import McpServer


def _transport(calls: list[httpx.Request], *, pending: bool) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/api/v1/paper/orders":
            if pending:
                return httpx.Response(
                    201,
                    json={
                        "id": "o1",
                        "status": "pending_approval",
                        "reason": "AWAITING_APPROVAL",
                        "approval_id": "a1",
                        "approval_status": "pending",
                    },
                )
            return httpx.Response(
                201,
                json={
                    "id": "o1",
                    "status": "filled",
                    "approval_id": None,
                    "approval_status": None,
                },
            )
        if path == "/api/v1/paper/approvals":
            return httpx.Response(
                200, json=[{"id": "a1", "status": "pending", "next_action": "approve_or_reject"}]
            )
        if path == "/api/v1/paper/automation":
            return httpx.Response(
                200,
                json={
                    "user_id": "default",
                    "tier": "manual",
                    "tiers": ["manual", "full_auto"],
                    "agent_orders": "wait_for_a_person",
                    "disclosure": {
                        "version": "v1",
                        "text": "You own the risk.",
                        "acknowledged_at": None,
                    },
                },
            )
        return httpx.Response(404, json={"detail": {"code": "NOT_FOUND", "message": path}})

    return httpx.MockTransport(handler)


@pytest.fixture
def calls() -> list[httpx.Request]:
    return []


def _call(server: McpServer, name: str, arguments: dict[str, Any]) -> Any:
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


ORDER = {
    "chain": "solana",
    "token_address": "So11111111111111111111111111111111111111112",
    "side": "buy",
    "amount_usd": "10",
    "idempotency_key": "mcp-approval-1",
}


def test_the_order_tool_returns_the_approval_when_one_waits(calls: list[httpx.Request]) -> None:
    client = EzchainClient("http://test", "token", transport=_transport(calls, pending=True))
    server = McpServer(client, allow_writes=True, credential=Credential("scoped"))

    body = _call(server, "ezchain_place_paper_order", ORDER)

    assert body["status"] == "pending_approval"
    assert body["approval_id"] == "a1"
    assert body["approval_status"] == "pending"
    assert body["executed"] is False
    assert body["next_action"] == "wait_for_a_person"


def test_the_order_tool_reports_a_fill_with_no_approval(calls: list[httpx.Request]) -> None:
    client = EzchainClient("http://test", "token", transport=_transport(calls, pending=False))
    server = McpServer(client, allow_writes=True, credential=Credential("scoped"))

    body = _call(server, "ezchain_place_paper_order", ORDER)

    assert body["status"] == "filled"
    assert body["executed"] is True
    assert body["approval_id"] is None
    assert body["next_action"] is None


def test_the_queue_and_the_tier_are_read_tools(calls: list[httpx.Request]) -> None:
    client = EzchainClient("http://test", "token", transport=_transport(calls, pending=True))
    server = McpServer(client, allow_writes=True, credential=Credential("scoped"))

    listed = _call(server, "ezchain_list_approvals", {"status": "pending"})
    tier = _call(server, "ezchain_get_automation", {})

    assert listed["result"][0]["id"] == "a1"
    assert tier["tier"] == "manual"
    assert tier["agent_orders"] == "wait_for_a_person"


def test_no_mcp_tool_decides_an_approval_or_changes_the_tier(
    calls: list[httpx.Request],
) -> None:
    """Deciding and opting in are a person's acts; an agent surface must not offer them."""
    client = EzchainClient("http://test", "token", transport=_transport(calls, pending=True))
    server = McpServer(client, allow_writes=True, credential=Credential("scoped"))
    response: Any = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {tool["name"] for tool in response["result"]["tools"]}

    assert "ezchain_list_approvals" in names
    assert "ezchain_get_automation" in names
    assert not any("approve" in name or "reject" in name for name in names)
    assert not any("automation" in name and "get_" not in name for name in names)
