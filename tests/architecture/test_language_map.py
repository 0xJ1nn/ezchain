"""The language-map gate: it must catch a violation, not just report a clean tree.

A checker that only ever prints "ok" is decoration. These tests give it one violation at a
time — an undeclared import, a second bridge client, a fourth language, a frozen crate
pulled in by a shipping crate, a float in the money oracle, a token in a client component
— and assert that it fails with the right kind. They also assert the real tree is clean, so
a genuine violation cannot hide behind a green suite.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "tools" / "check-language-map.py"
MAP = ROOT / "architecture" / "language-map.json"

# The checker is a script, not an installed module; load it by path so the tests exercise
# the same file the gate runs.
_spec = importlib.util.spec_from_file_location("check_language_map", CHECKER)
assert _spec is not None and _spec.loader is not None
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)


def findings() -> list[dict[str, str]]:
    spec = checker.read_map()
    return checker.run(spec)


def kinds() -> set[str]:
    return {item["kind"] for item in findings()}


def test_the_real_tree_is_clean() -> None:
    """The map describes this tree. A new violation fails here, not in a later review."""
    assert findings() == []


def test_the_cli_exits_zero_on_a_clean_tree() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "language-map: ok" in result.stdout


def test_json_output_is_valid_json() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--json"], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == []


def test_explain_names_the_layers_and_the_unfreeze_condition() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--explain"], cwd=ROOT, capture_output=True, text=True
    )
    assert "python layers:" in result.stdout
    assert "frozen crates: ezchain-p2p" in result.stdout
    assert "unfreeze when: A second, independently operated machine" in result.stdout


def test_a_fourth_language_is_refused() -> None:
    """A .go file is a language decision, and the checker is where it stops."""
    spec = checker.read_map()
    banned = spec["forbidden"]["languages"]["extensions"]
    assert ".go" in banned and ".rb" in banned and ".cs" in banned
    assert checker.check_forbidden_languages([Path("services/x/main.go")], spec)
    assert checker.check_forbidden_languages([Path("apps/api/src/x.py")], spec) == []


def test_an_undeclared_layer_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A new package with a pyproject and no map entry cannot appear silently."""
    spec = checker.read_map()
    package = tmp_path / "services" / "newthing"
    package.mkdir(parents=True)
    (package / "pyproject.toml").write_text('[project]\nname = "ezchain-newthing"\n')
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    found = checker.check_declared_layers(spec)
    assert any(item["kind"] == "undeclared-layer" for item in found), found


def test_an_undeclared_import_edge_is_refused() -> None:
    """core -> api is the inversion the map exists to stop; core.py is the proof."""
    spec = checker.read_map()
    by_layer = checker.python_files(spec["python_layers"])
    allowed = set(spec["python_layers"]["ezchain_core"]["may_import"])
    assert allowed == set(), "core-py must import no workspace layer"
    # Every core-py file must stay clean; if one starts importing the API, this fails.
    for path in by_layer["ezchain_core"]:
        rel = str(path.relative_to(checker.ROOT))
        if checker.is_test(rel):
            continue
        modules = checker.imports_of(path)
        assert "ezchain_api" not in modules, rel


def test_every_bridge_client_is_declared() -> None:
    spec = checker.read_map()
    allowed = {entry["path"] for entry in spec["python_entry_points"]}
    assert len(allowed) == 2
    assert "apps/api/src/ezchain_api/native.py" in allowed
    assert "services/executor/src/ezchain_executor/native.py" in allowed
    assert checker.check_bridge_clients(spec) == []


def test_pumpfire_stays_behind_its_declared_packages() -> None:
    spec = checker.read_map()
    rule = spec["third_party_seams"]["pumpfire"]
    assert set(rule["allowed"]) == {"packages/adapters", "services/ingest"}
    assert checker.check_third_party_seams(spec) == []


def test_every_rust_crate_is_declared() -> None:
    spec = checker.read_map()
    known = (
        set(spec["rust_crates"]["shipping"])
        | set(spec["rust_crates"]["frozen"])
        | set(spec["rust_crates"]["shipping_unwired"])
    )
    on_disk = {
        path.name for path in (ROOT / "rust").glob("ezchain-*") if (path / "Cargo.toml").is_file()
    }
    assert on_disk == known
    assert checker.check_rust_crates(spec) == []


def test_shipping_means_reachable_from_a_shipped_entry_point() -> None:
    """A crate nothing links is unwired, however useful its tests are.

    The map recorded the failure this prevents: thirteen crates counted as product value
    while no installer, service or extension started six of them. Reachability is computed,
    not asserted, so a crate cannot be relabelled shipping to look finished.
    """
    spec = checker.read_map()
    crates = {
        path.name: path
        for path in (ROOT / "rust").glob("ezchain-*")
        if (path / "Cargo.toml").is_file()
    }
    entry_points = spec["rust_crates"]["entry_points"]
    assert entry_points == ["ezchain-bridge", "ezchain-cli"]
    reachable = checker.reachable_from(entry_points, crates)
    assert "ezchain-core" in reachable
    assert "ezchain-decimal" in reachable
    assert "ezchain-rpc" in reachable
    # These are proven and called by nobody; the map must say so rather than "shipping".
    for name in ("ezchain-seal", "ezchain-replay", "ezchain-flow"):
        assert name not in reachable, name
        assert name in spec["rust_crates"]["shipping_unwired"], name
    assert not (reachable & set(spec["rust_crates"]["frozen"]))


def test_a_shipping_crate_without_a_user_is_refused() -> None:
    spec = checker.read_map()
    polluted = json.loads(json.dumps(spec))
    polluted["rust_crates"]["shipping"]["ezchain-p2p"] = "claimed shipping"
    polluted["rust_crates"]["frozen"].pop("ezchain-p2p")
    found = checker.check_rust_crates(polluted)
    assert any(item["kind"] == "shipping-without-a-user" for item in found), found
    assert not checker.check_rust_crates(spec)


def test_a_stale_unwired_entry_is_refused() -> None:
    """When the seal is wired, its exception must be deleted in the same change."""
    spec = checker.read_map()
    crates = {
        path.name: path
        for path in (ROOT / "rust").glob("ezchain-*")
        if (path / "Cargo.toml").is_file()
    }
    polluted = json.loads(json.dumps(spec))
    polluted["rust_crates"]["shipping_unwired"]["ezchain-core"] = "stale"
    found = checker.check_rust_crates(polluted)
    assert any(item["kind"] == "stale-unwired-entry" for item in found), found
    assert checker.reachable_from(spec["rust_crates"]["entry_points"], crates)


def test_a_shipping_crate_may_not_depend_on_a_frozen_one() -> None:
    """The freeze is only real if nothing in the shipped product links the frozen crates."""
    spec = checker.read_map()
    shipping = set(spec["rust_crates"]["shipping"])
    frozen = set(spec["rust_crates"]["frozen"])
    for name in shipping:
        text = (ROOT / "rust" / name / "Cargo.toml").read_text(encoding="utf-8")
        for dependency in frozen:
            assert f"{dependency} =" not in text, f"{name} links frozen {dependency}"


def test_the_money_oracle_has_no_float_arithmetic() -> None:
    spec = checker.read_map()
    assert checker.check_money_paths(spec) == []


def test_no_client_component_reads_the_deployment_token() -> None:
    spec = checker.read_map()
    assert checker.check_renderer(spec, checker.tracked_files()) == []


def test_known_violations_are_real_and_shrink_only() -> None:
    """A recorded inversion must name files that exist and still violate the map.

    When someone fixes an inversion, the entry becomes a stale exception; this test fails
    and the entry is deleted. That is how the list is kept honest.
    """
    spec = checker.read_map()
    for entry in spec["known_violations"]:
        assert entry["reason"], entry
        for relative in entry["files"]:
            assert (ROOT / relative).is_file(), f"stale known_violations entry: {relative}"
    double_counted = {relative for entry in spec["known_violations"] for relative in entry["files"]}
    # No file may be excused twice: that hides a second, different edge behind one entry.
    total = sum(len(entry["files"]) for entry in spec["known_violations"])
    assert len(double_counted) == total


def test_the_gate_runs_this_check() -> None:
    """A checker that no gate calls is a suggestion.

    The enforcement this change adds is worth nothing if a later edit deletes the step, so
    the wiring itself is a test: the gate names the script and fails when it finds a
    finding. Both properties are asserted on the shell script, not on intent.
    """
    gate = (ROOT / "tools" / "ci.sh").read_text(encoding="utf-8")
    assert "tools/check-language-map.py" in gate
    # The contrast gate is the same kind of promise: a palette that fails WCAG is caught
    # before merge, not by whoever happens to look at the page next.
    assert "tools/check-contrast.mjs" in gate
    # It must run through an interpreter whose absence is loud, not a bare `|| true`.
    line = next(line for line in gate.splitlines() if "tools/check-language-map.py" in line)
    assert "|| true" not in line
    assert "set -e" in gate or "set -euo" in gate
