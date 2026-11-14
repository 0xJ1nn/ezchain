"""The model sees the MCP server's list and nothing else."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from agent_helpers import (
    ACCOUNT,
    ACCOUNT_RESULT,
    SCOPED_TOOLS,
    FakeMcp,
    FakeModel,
    FixedClock,
    make_limits,
    tool,
)

from ezchain_agent_runtime import (
    AgentLoop,
    Answer,
    McpError,
    McpTool,
    ModelTurn,
    SessionState,
    StopReason,
    ToolCall,
    ToolOutOfScope,
    ToolSurface,
    ToolSurfaceError,
)

FORBIDDEN_TOOL_NAMES = {
    "shell",
    "bash",
    "exec",
    "run_command",
    "subprocess",
    "fetch",
    "fetch_url",
    "http_get",
    "download",
    "read_file",
    "write_file",
    "delete_file",
    "open",
}

FORBIDDEN_IMPORTS = {
    "ctypes",
    "http",
    "httpx",
    "pathlib",
    "requests",
    "shutil",
    "socket",
    "subprocess",
    "tempfile",
    "urllib",
    "webbrowser",
}
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "system", "popen"}

PACKAGE = Path(__file__).resolve().parents[2] / "services" / "agent-runtime" / "src"


def test_the_surface_is_exactly_the_servers_list_in_order() -> None:
    mcp = FakeMcp()
    surface = ToolSurface.from_toolbox(mcp)

    assert surface.names() == tuple(tool.name for tool in SCOPED_TOOLS)
    assert [(item["name"], item["inputSchema"]) for item in surface.model_tools()] == [
        (item.name, dict(item.input_schema)) for item in SCOPED_TOOLS
    ]


def test_the_loop_hands_the_model_the_surface_and_no_built_in_tools() -> None:
    model = FakeModel([ModelTurn(answer=Answer(summary="no numbers"))])
    loop = AgentLoop(
        model=model,
        toolbox=FakeMcp(),
        limits=make_limits(),
        clock=FixedClock(),
    )
    loop.run("hello")

    offered = {item["name"] for item in model.requests[0].tools}
    assert offered == set(ToolSurface.from_toolbox(FakeMcp()).names())
    assert offered.isdisjoint(FORBIDDEN_TOOL_NAMES)


def test_a_call_outside_the_list_is_refused_without_reaching_the_server() -> None:
    mcp = FakeMcp(results={ACCOUNT.name: ACCOUNT_RESULT})
    model = FakeModel(
        [ModelTurn(tool_calls=(ToolCall(id="c1", name="shell", arguments={"cmd": "ls"}),))]
    )
    outcome = AgentLoop(
        model=model,
        toolbox=mcp,
        limits=make_limits(),
        clock=FixedClock(),
    ).run("list the files")

    assert outcome.state is SessionState.FAILED
    assert outcome.reason is StopReason.TOOL_OUT_OF_SCOPE
    assert "shell" in (outcome.detail or "")
    assert mcp.calls == []


def test_require_rejects_a_name_the_server_did_not_list() -> None:
    surface = ToolSurface.from_toolbox(FakeMcp())

    with pytest.raises(ToolOutOfScope):
        surface.require("fetch_url")


def test_a_broken_tool_list_is_not_a_tool_surface() -> None:
    with pytest.raises(ToolSurfaceError):
        ToolSurface.from_toolbox(FakeMcp(tools=(tool(""),))).names()
    with pytest.raises(ToolSurfaceError):
        ToolSurface.from_toolbox(FakeMcp(tools=(ACCOUNT, ACCOUNT)))


def test_an_unreachable_server_fails_the_session_closed() -> None:
    model = FakeModel([ModelTurn(answer=Answer(summary="never"))])
    outcome = AgentLoop(
        model=model,
        toolbox=FakeMcp(list_error=McpError("child exited")),
        limits=make_limits(),
        clock=FixedClock(),
    ).run("hello")

    assert outcome.state is SessionState.FAILED
    assert outcome.reason is StopReason.MCP_UNREACHABLE
    assert model.requests == []


def _imported_names(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_the_package_imports_no_shell_url_or_filesystem_module() -> None:
    # The trace store is the one module allowed a filesystem import: it writes one append-only
    # file per session and reads nothing else. Every module that can reach a tool decision
    # stays clean, and the trace's own surface is pinned in test_trace_append_only.
    sources = [source for source in PACKAGE.rglob("*.py") if source.name != "trace.py"]
    assert sources
    for source in sources:
        names = _imported_names(source.read_text())
        assert names.isdisjoint(FORBIDDEN_IMPORTS), f"{source.name} imports {names}"


def test_only_the_trace_store_reaches_the_filesystem() -> None:
    for source in PACKAGE.rglob("*.py"):
        names = _imported_names(source.read_text())
        if source.name == "trace.py":
            assert "pathlib" in names
        else:
            assert names.isdisjoint({"os", "pathlib", "tempfile"}), f"{source.name} imports {names}"


def test_the_package_calls_no_dynamic_code_primitive() -> None:
    for source in PACKAGE.rglob("*.py"):
        tree = ast.parse(source.read_text())
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert called.isdisjoint(FORBIDDEN_CALLS), f"{source.name} calls {called}"


def test_mcp_tool_carries_only_the_contract_fields() -> None:
    assert set(McpTool.__dataclass_fields__) == {"name", "description", "input_schema"}
