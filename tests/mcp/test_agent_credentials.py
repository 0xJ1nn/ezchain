"""The MCP write surface only ever opens for a scoped, revocable key.

The deployment token carries the ``admin`` scope, so it can release the kill switch and
reload plugins. An agent holding it is an operator by another name. The server probes one
admin-only read route: ``admin`` answers 200, a scoped key answers 403. Everything else is
unknown, and unknown fails closed.
"""

from __future__ import annotations

import io
from typing import Any

import httpx
import pytest

import ezchain_mcp.cli as cli
from ezchain_mcp.client import ApiError, EzchainClient
from ezchain_mcp.credentials import (
    ADMIN_PROBE_PATH,
    Credential,
    inspect_credential,
    refusal_message,
)
from ezchain_mcp.server import McpServer


@pytest.fixture
def calls() -> list[httpx.Request]:
    return []


def _client(
    status: int = 403,
    *,
    calls: list[httpx.Request] | None = None,
    code: str = "FORBIDDEN",
) -> EzchainClient:
    recorded = calls if calls is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        detail = {"code": code, "message": "the API refused the probe", "retryable": False}
        return httpx.Response(status, json={"detail": detail})

    return EzchainClient("http://test", "credential", transport=httpx.MockTransport(handler))


def _admin_client(calls: list[httpx.Request] | None = None) -> EzchainClient:
    return _client(200, calls=calls)


def test_the_probe_asks_an_admin_only_route(calls: list[httpx.Request]) -> None:
    client = _admin_client(calls)
    inspect_credential(client, "credential")

    assert [request.url.path for request in calls] == [ADMIN_PROBE_PATH]
    assert calls[0].headers["authorization"] == "Bearer credential"


def test_admin_credential_is_admin(calls: list[httpx.Request]) -> None:
    credential = inspect_credential(_admin_client(calls), "credential")

    assert credential.status == "admin"
    assert credential.may_write is False


def test_scoped_credential_may_write(calls: list[httpx.Request]) -> None:
    credential = inspect_credential(_client(403, calls=calls), "credential")

    assert credential.status == "scoped"
    assert credential.may_write is True


def test_missing_credential_is_not_probed(calls: list[httpx.Request]) -> None:
    credential = inspect_credential(_client(403, calls=calls), None)

    assert credential.status == "missing"
    assert calls == []


def test_invalid_credential_fails_closed(calls: list[httpx.Request]) -> None:
    credential = inspect_credential(_client(401, calls=calls, code="UNAUTHORIZED"), "revoked")

    assert credential.status == "unverified"
    assert credential.may_write is False


def test_unreachable_api_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = EzchainClient("http://test", "credential", transport=httpx.MockTransport(handler))

    assert inspect_credential(client, "credential").status == "unverified"


def test_refusal_explains_how_to_mint_a_scoped_key() -> None:
    message = refusal_message(Credential("admin"))

    assert "admin scope" in message
    assert ADMIN_PROBE_PATH in message
    assert '"scopes":["read","trade"]' in message
    assert "deployment token" in message


def test_unverified_refusal_carries_the_reason() -> None:
    message = refusal_message(Credential("unverified", "401 UNAUTHORIZED: revoked"))

    assert "401 UNAUTHORIZED: revoked" in message
    assert ADMIN_PROBE_PATH in message


def test_refused_server_hides_writes_and_explains_why(calls: list[httpx.Request]) -> None:
    refusal = refusal_message(Credential("admin"))
    server = McpServer(_admin_client(calls), allow_writes=False, write_refusal=refusal)

    listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert listed is not None
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert "ezchain_place_paper_order" not in names

    initialized = server.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}})
    assert initialized is not None
    instructions = initialized["result"]["instructions"]
    assert "writes are disabled" in instructions.lower()
    assert ADMIN_PROBE_PATH in instructions


def test_the_admin_surface_has_no_agent_tool(calls: list[httpx.Request]) -> None:
    """Arming, releasing and reloading are admin routes; a scoped key cannot call them."""
    server = McpServer(_admin_client(calls), allow_writes=True)
    listed = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    assert listed is not None

    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert "ezchain_place_paper_order" in names
    assert not names & {
        "ezchain_arm_kill_switch",
        "ezchain_disarm_kill_switch",
        "ezchain_reload_plugins",
    }


class _ProbeClient:
    def __init__(self, error: ApiError | None = None) -> None:
        self.error = error
        self.probes = 0
        self.closed = False

    def get(self, path: str) -> Any:
        self.probes += 1
        assert path == ADMIN_PROBE_PATH
        if self.error is not None:
            raise self.error
        return [{"id": "k1"}]

    def close(self) -> None:
        self.closed = True


def _fake_server(seen: dict[str, Any], handle: Any = None) -> Any:
    class FakeServer:
        def __init__(
            self,
            client: Any,
            *,
            allow_writes: bool = False,
            write_refusal: str | None = None,
            **_kwargs: Any,
        ) -> None:
            seen["allow_writes"] = allow_writes
            seen["write_refusal"] = write_refusal

        def handle(self, message: Any) -> Any:
            return handle(message) if handle is not None else None

    return FakeServer


def test_cli_refuses_writes_for_an_admin_credential(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}
    client = _ProbeClient()
    monkeypatch.setattr(cli, "EzchainClient", lambda base_url, token: client)
    monkeypatch.setattr(cli, "McpServer", _fake_server(seen))
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    code = cli.main(["--api-url", "http://test", "--token", "deployment-token", "--allow-writes"])

    assert code == 0
    assert seen["allow_writes"] is False
    assert ADMIN_PROBE_PATH in seen["write_refusal"]
    assert "writes refused" in capsys.readouterr().err
    assert client.closed is True


def test_cli_keeps_writes_for_a_scoped_credential(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}
    client = _ProbeClient(ApiError(403, "FORBIDDEN", "this principal lacks the 'admin' scope"))
    monkeypatch.setattr(cli, "EzchainClient", lambda base_url, token: client)
    monkeypatch.setattr(cli, "McpServer", _fake_server(seen))
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    code = cli.main(["--api-url", "http://test", "--token", "ezk_scoped", "--allow-writes"])

    assert code == 0
    assert seen["allow_writes"] is True
    assert seen["write_refusal"] is None
    assert capsys.readouterr().err == ""


def test_cli_read_only_never_probes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}
    client = _ProbeClient()
    monkeypatch.setattr(cli, "EzchainClient", lambda base_url, token: client)
    monkeypatch.setattr(cli, "McpServer", _fake_server(seen))
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    code = cli.main(["--api-url", "http://test", "--token", "deployment-token", "--read-only"])

    assert code == 0
    assert client.probes == 0
    assert seen["allow_writes"] is False
    assert seen["write_refusal"] is None
