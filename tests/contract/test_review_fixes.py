"""Regressions for the contract-gate review findings.

An ``--update`` must never erase the evidence that a change was unbumped, and the SDK
snapshot must include public properties, not just methods.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.contract import capture, gate


class _Example:
    @property
    def public_property(self) -> str:
        return "value"


def test_capture_includes_public_properties() -> None:
    info = capture._capture_class(_Example)
    assert info["methods"]["public_property"] == "<property>"


def test_allows_update_only_for_a_bumped_version() -> None:
    assert gate.allows_update(["the openapi snapshots are stale: they record 0.0.0"])
    assert not gate.allows_update(["the openapi contract changed without a version bump"])
    assert not gate.allows_update([
        "the openapi snapshots are stale: they record 0.0.0",
        "deprecation for sdk_interfaces:field has no warning text",
    ])


def test_update_refuses_to_overwrite_a_failing_baseline(
    monkeypatch: object, tmp_path: Path
) -> None:
    import tools.check_contracts as checker

    snapshot_dir = tmp_path / "snapshots"
    snapshot_dir.mkdir()
    previous = {"openapi": {"info": {"title": "old"}}, "mcp_tools": {}, "sdk_interfaces": {}}
    for surface, payload in previous.items():
        (snapshot_dir / f"{surface}.json").write_text(json.dumps(payload))
    (snapshot_dir / "manifest.json").write_text(json.dumps({"version": "0.0.0"}))
    (tmp_path / "deprecations.json").write_text("[]")

    monkeypatch.setattr(checker, "SNAPSHOT_DIR", snapshot_dir)  # type: ignore[attr-defined]
    monkeypatch.setattr(checker, "MANIFEST_FILE", snapshot_dir / "manifest.json")  # type: ignore[attr-defined]
    monkeypatch.setattr(checker, "DEPRECATIONS_FILE", tmp_path / "deprecations.json")  # type: ignore[attr-defined]
    monkeypatch.setattr(checker, "__version__", "0.0.0")  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        checker,
        "capture_all",
        lambda: {**previous, "openapi": {"info": {"title": "new"}}},
    )

    assert checker.update() == 1
    assert json.loads((snapshot_dir / "openapi.json").read_text()) == previous["openapi"]
