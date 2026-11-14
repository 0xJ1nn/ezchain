"""The harness is a front end, not a second loop.

Two things must hold for the command to be the panel's peer rather than a fork of it: it
builds the runtime's own loop class, and it holds none of the loop's decisions. A result
that waits for a person, a refusal that stops the session and a budget that is spent are
all read inside ``AgentLoop``; a string comparison here would be a second implementation.
"""

from __future__ import annotations

import ast
from pathlib import Path

from ezchain_agent_runtime import AgentLoop as RuntimeAgentLoop
from ezchain_harness import cli

PACKAGE = Path(__file__).resolve().parents[2] / "services" / "harness" / "src"
FORBIDDEN_PACKAGES = {
    "ezchain_core",
    "ezchain_api",
    "ezchain_engine",
    "ezchain_executor",
    "ezchain_mcp",
    "ezchain_harness_provider",
}
LOOP_OWNED_STRINGS = {
    "pending_approval",
    "approval_status",
    "KILL_SWITCH_ACTIVE",
    "KILL_SWITCH_ARMED",
}


def _imports(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def _literals(source: str) -> set[str]:
    return {
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def test_the_command_builds_the_runtimes_own_loop() -> None:
    assert cli.AgentLoop is RuntimeAgentLoop


def test_the_harness_imports_no_platform_package() -> None:
    sources = sorted(PACKAGE.rglob("*.py"))

    assert sources
    for source in sources:
        imported = _imports(source.read_text(encoding="utf-8"))
        assert imported.isdisjoint(FORBIDDEN_PACKAGES), f"{source.name} imports {imported}"


def test_the_harness_holds_no_loop_decision() -> None:
    for source in sorted(PACKAGE.rglob("*.py")):
        literals = _literals(source.read_text(encoding="utf-8"))
        assert literals.isdisjoint(LOOP_OWNED_STRINGS), (
            f"{source.name} names a loop decision; the loop must be the only reader"
        )


def test_the_harness_fires_no_session_event_itself() -> None:
    for source in sorted(PACKAGE.rglob("*.py")):
        text = source.read_text(encoding="utf-8")
        assert "SessionEvent." not in text, f"{source.name} drives the lifecycle"
        assert ".fire(" not in text, f"{source.name} drives the lifecycle"
