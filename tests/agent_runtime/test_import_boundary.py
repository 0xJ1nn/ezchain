"""ADR-0042: the runtime imports no platform package.

The boundary forbids ``ezchain_core`` even for the refusal codes the loop reacts to, so the
codes travel as plain strings in the MCP result and the two the loop acts on live in a local
table. These tests pin that table to the platform's own code list from the outside.
"""

from __future__ import annotations

import ast
from pathlib import Path

from ezchain_agent_runtime import KILL_SWITCH_CODES
from ezchain_core.types import RISK_REASON_MESSAGES

PACKAGE = Path(__file__).resolve().parents[2] / "services" / "agent-runtime" / "src"
FORBIDDEN_PACKAGES = {"ezchain_core", "ezchain_api", "ezchain_engine", "ezchain_executor"}


def _top_level_imports(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_the_runtime_imports_no_platform_package() -> None:
    sources = list(PACKAGE.rglob("*.py"))
    assert sources
    for source in sources:
        imported = _top_level_imports(source.read_text())
        assert imported.isdisjoint(FORBIDDEN_PACKAGES), f"{source.name} imports {imported}"


def test_the_kill_switch_codes_are_the_platforms_own_codes() -> None:
    assert KILL_SWITCH_CODES
    assert KILL_SWITCH_CODES <= set(RISK_REASON_MESSAGES)
