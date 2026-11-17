"""The published API, MCP and plugin surfaces must not move without a version bump."""

from __future__ import annotations

import json
from typing import Any

from ezchain_sdk import __all__ as SDK_PUBLIC_NAMES

from .capture import SURFACES, capture_all
from .gate import DEPRECATIONS_FILE, MANIFEST_FILE, SNAPSHOT_DIR, evaluate


def _load_json(path: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_snapshots_match_the_live_contract() -> None:
    current = capture_all()
    manifest = _load_json(MANIFEST_FILE)
    previous = {surface: _load_json(SNAPSHOT_DIR / f"{surface}.json") for surface in SURFACES}
    deprecations = _load_json(DEPRECATIONS_FILE)

    problems = evaluate(current, previous, manifest, deprecations, manifest["version"])
    assert problems == [], "\n\n".join(problems)


def test_the_manifest_uses_the_api_version_constant() -> None:
    from ezchain_api import __version__

    manifest = _load_json(MANIFEST_FILE)
    assert manifest["version"] == __version__
    assert manifest["version_source"] == "ezchain_api.__version__"


def test_every_mcp_tool_is_snapshotted() -> None:
    from ezchain_mcp.tools import all_tools

    snapshot = _load_json(SNAPSHOT_DIR / "mcp_tools.json")
    assert set(snapshot) == {tool.name for tool in all_tools(True)}


def test_every_public_sdk_name_is_snapshotted() -> None:
    snapshot = _load_json(SNAPSHOT_DIR / "sdk_interfaces.json")
    assert set(SDK_PUBLIC_NAMES) <= set(snapshot)
    assert "manifest.SDK_VERSION" in snapshot
    assert "manifest.TOP_LEVEL_KEYS" in snapshot


def test_capture_is_deterministic() -> None:
    assert capture_all() == capture_all()


def test_snapshots_are_non_empty() -> None:
    current = capture_all()
    for surface in SURFACES:
        assert current[surface], f"the {surface} snapshot must not be empty"
