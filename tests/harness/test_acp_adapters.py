"""Criterion (a): an adapter is a stdio launch with only the platform MCP server attached.

The three adapters differ only in how the external agent process is started. Everything a
session is allowed to reach is decided by the client, so these tests pin the launch command,
the single MCP server and the absence of file, shell and terminal capabilities.
"""

from __future__ import annotations

import io
import os
import subprocess

import pytest

import ezchain_harness.acp.client as acp_client
from ezchain_harness.acp import (
    ADAPTERS,
    CLAUDE_CODE,
    PLATFORM_MCP_NAME,
    SUPPORTED_ADAPTERS,
    AcpClient,
    client_capabilities,
    launch_agent,
    platform_mcp_servers,
    supported_adapters,
)

from .acp_helpers import FakeAgent, RecordingBroker


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.name)
def test_each_adapter_starts_over_stdio_with_only_the_platform_mcp(adapter) -> None:
    assert adapter.command, "an adapter must name the process to start"
    assert adapter.command[0] != "/bin/sh", "the agent must start without a shell"
    assert "bash" not in adapter.command and "sh -c" not in adapter.command

    servers = platform_mcp_servers()
    assert len(servers) == 1, "only the platform's MCP server may be attached"
    assert servers[0]["name"] == PLATFORM_MCP_NAME
    assert servers[0]["command"], "the MCP server needs a launch command"
    assert "env" not in servers[0], "the MCP config must not carry a key from the harness"


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.name)
def test_adapter_pins_the_bridge_package_version(adapter) -> None:
    command = list(adapter.command)
    if command[0] == "npx":
        assert "-y" in command
        package = command[command.index("-y") + 1]
        assert "@" in package and not package.endswith("@latest"), (
            "the ACP bridge must be version-pinned, never a floating tag"
        )
    else:
        assert adapter.name == "opencode" and command == ["opencode", "acp"]


def test_no_filesystem_or_terminal_capability_is_advertised() -> None:
    caps = client_capabilities()
    assert "fs" not in caps and "terminal" not in caps
    assert "mcp" in caps, "the platform MCP server is the one capability the agent gets"


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.name)
def test_session_new_carries_exactly_the_platform_mcp_config(adapter) -> None:
    agent = FakeAgent()
    client = AcpClient(agent, broker=RecordingBroker())
    client.initialize()
    client.new_session()

    new_session = next(m for m in agent.client_messages if m.get("method") == "session/new")
    assert new_session["params"]["mcpServers"] == list(platform_mcp_servers())
    for key in ("env", "HTTP_PROXY", "token", "api_key"):
        assert key not in repr(new_session["params"]).lower()


def test_supported_list_contains_only_injection_verified_adapters() -> None:
    assert supported_adapters() == tuple(a.name for a in ADAPTERS if a.injection_verified)
    assert SUPPORTED_ADAPTERS == supported_adapters()
    assert set(supported_adapters()) <= {a.name for a in ADAPTERS}


def test_launch_agent_starts_the_process_over_pipes_without_a_shell(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakePopen:
        def __init__(self, command, **kwargs) -> None:
            calls["command"] = command
            calls["kwargs"] = kwargs
            self.stdin = io.StringIO()
            self.stdout = io.StringIO()

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(acp_client.subprocess, "Popen", FakePopen)
    rpc = launch_agent(CLAUDE_CODE)

    assert calls["command"] == list(CLAUDE_CODE.command)
    kwargs = calls["kwargs"]
    assert kwargs["stdin"] == subprocess.PIPE
    assert kwargs["stdout"] == subprocess.PIPE
    assert kwargs["shell"] is False
    rpc.close()


def test_a_fake_credential_in_the_environment_never_reaches_the_agent(monkeypatch) -> None:
    secret = "sk-acp-test-do-not-use-1234567890"
    monkeypatch.setenv("EZCHAIN_TEST_SECRET", secret)
    agent = FakeAgent()
    client = AcpClient(agent, broker=RecordingBroker())
    client.initialize()
    client.new_session()

    assert secret not in repr(agent.client_messages)
    assert os.environ.get("EZCHAIN_TEST_SECRET") == secret
