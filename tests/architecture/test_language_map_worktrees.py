"""A checkout inside a checkout must not become product source to the gate.

The language-map check describes the repository it is run in. A worktree kept under the
main checkout duplicates every package and module; a scan that walks the directory tree
instead of asking git which files belong to the checkout reports those copies as new
layers and imports and blocks the gate. These tests build a small checkout with a nested
copy and assert it changes nothing, then place a real violation in a tracked file and
assert the check still fails.

Only the file-discovery paths are exercised here. The checks themselves are covered in
``test_language_map.py``.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "tools" / "check-language-map.py"

# The checker is a script, not an installed module; load it by path so the tests exercise
# the same file the gate runs.
_spec = importlib.util.spec_from_file_location("check_language_map", CHECKER)
assert _spec is not None and _spec.loader is not None
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)


def _init_repo(root: Path) -> None:
    """A tiny real checkout: git, and an ignore rule for nested worktrees."""
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    (root / ".gitignore").write_text(".claude/\n.worktrees/\n", encoding="utf-8")


def _write_checkout(root: Path, spec: dict) -> None:
    """Create the declared paths so the fixture has no findings of its own."""
    for name, entry in spec["python_layers"].items():
        package = root / entry["path"]
        package.mkdir(parents=True, exist_ok=True)
        source = ROOT / entry["path"] / "pyproject.toml"
        text = (
            source.read_text(encoding="utf-8")
            if source.is_file()
            else f'[project]\nname = "{name.replace("_", "-")}"\n'
        )
        (package / "pyproject.toml").write_text(text, encoding="utf-8")

    rust = spec["rust_crates"]
    for group in ("shipping", "frozen", "shipping_unwired"):
        for crate in rust[group]:
            manifest = root / "rust" / crate / "Cargo.toml"
            manifest.parent.mkdir(parents=True, exist_ok=True)
            original = ROOT / "rust" / crate / "Cargo.toml"
            text = (
                original.read_text(encoding="utf-8")
                if original.is_file()
                else '[package]\nname = "test"\nversion = "0.1.0"\n'
            )
            manifest.write_text(text, encoding="utf-8")


def _add_nested_checkout(root: Path, spec: dict) -> None:
    """A worktree parked under the checkout repeats its packages and product code."""
    nested = root / ".claude" / "worktrees" / "duplicate"
    _write_checkout(nested, spec)
    leak = nested / "services" / "ingest" / "src" / "ezchain_ingest" / "leak.py"
    leak.parent.mkdir(parents=True, exist_ok=True)
    leak.write_text("import pumpfire\n", encoding="utf-8")


def test_a_nested_checkout_does_not_change_the_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = checker.read_map()
    _init_repo(tmp_path)
    _write_checkout(tmp_path, spec)
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    before = checker.run(spec)
    _add_nested_checkout(tmp_path, spec)
    after = checker.run(spec)
    assert after == before, [item for item in after if item not in before]


def test_an_undeclared_package_in_a_tracked_file_still_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = checker.read_map()
    _init_repo(tmp_path)
    _write_checkout(tmp_path, spec)
    ghost = tmp_path / "services" / "ghost"
    ghost.mkdir(parents=True)
    (ghost / "pyproject.toml").write_text('[project]\nname = "ezchain-ghost"\n', encoding="utf-8")
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    found = checker.run(spec)
    assert any(
        item["kind"] == "undeclared-layer" and item["where"] == "services/ghost" for item in found
    ), found


def test_a_forbidden_import_in_a_tracked_file_still_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = checker.read_map()
    _init_repo(tmp_path)
    _write_checkout(tmp_path, spec)
    leak = tmp_path / "apps" / "mcp" / "src" / "ezchain_mcp" / "leak.py"
    leak.parent.mkdir(parents=True, exist_ok=True)
    leak.write_text("import pumpfire\n", encoding="utf-8")
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    found = checker.run(spec)
    assert any(
        item["kind"] == "third-party-seam" and item["where"] == "apps/mcp/src/ezchain_mcp/leak.py"
        for item in found
    ), found


def test_the_file_set_fallback_skips_hidden_dirs_and_nested_checkouts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without git, the walk ignores hidden directories and directories that are repos."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    (tmp_path / "visible.py").write_text("", encoding="utf-8")
    hidden = tmp_path / ".claude" / "worktrees" / "clone"
    hidden.mkdir(parents=True)
    (hidden / "ghost.py").write_text("", encoding="utf-8")
    nested_repo = tmp_path / "vendor" / "other"
    nested_repo.mkdir(parents=True)
    (nested_repo / ".git").write_text("gitdir: /nowhere\n", encoding="utf-8")
    (nested_repo / "nested.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    visible = {str(path) for path in checker.tracked_files()}
    assert "visible.py" in visible
    assert not any(path.startswith(".claude/") for path in visible)
    assert "vendor/other/nested.py" not in visible
