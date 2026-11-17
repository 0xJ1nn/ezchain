"""Version gate for the published contract surfaces.

A snapshot is a promise to clients. When a surface changes, the promise has moved and the
API version must move with it; a field may only be removed after one whole minor version in
which it still exists and advertises its deprecation. This module holds the pure decision
logic so the gate can be tested on synthetic contracts as well as the real snapshots.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
MANIFEST_FILE = SNAPSHOT_DIR / "manifest.json"
DEPRECATIONS_FILE = Path(__file__).parent / "deprecations.json"
SURFACES = ("openapi", "mcp_tools", "sdk_interfaces")
WARNING_KEYS = ("deprecated", "warning", "description")
STALE_SNAPSHOT_MESSAGE = "snapshots are stale"


def allows_update(problems: list[str]) -> bool:
    """Whether the only reason to refresh is that the version already moved.

    ``--update`` must not erase a gate failure. A stale-snapshot message is expected after
    a reviewed version bump; anything else — an unbumped change, a missing deprecated field
    or a bad deprecation window — means the old baseline still has evidence to protect.
    """
    return all(STALE_SNAPSHOT_MESSAGE in problem for problem in problems)


@dataclass(frozen=True)
class Change:
    """One field that differs between a live surface and its snapshot."""

    surface: str
    path: str
    kind: str
    old: Any = None
    new: Any = None

    def __str__(self) -> str:
        if self.kind == "added":
            return f"{self.surface}: added {self.path} = {_short(self.new)}"
        if self.kind == "removed":
            return f"{self.surface}: removed {self.path} = {_short(self.old)}"
        return f"{self.surface}: changed {self.path}: {_short(self.old)} -> {_short(self.new)}"


def _short(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=True, default=repr)
    return text if len(text) <= 80 else text[:77] + "..."


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Name every leaf of a JSON-like value, including list lengths and indices."""
    leaves: dict[str, Any] = {}
    if isinstance(value, dict):
        for key in sorted(value, key=str):
            path = f"{prefix}.{key}" if prefix else str(key)
            leaves.update(flatten(value[key], path))
    elif isinstance(value, (list, tuple)):
        leaves[f"{prefix}#length"] = len(value)
        for index, item in enumerate(value):
            leaves.update(flatten(item, f"{prefix}[{index}]"))
    else:
        leaves[prefix] = value
    return leaves


def surface_diff(surface: str, current: Any, previous: Any) -> list[Change]:
    """Report added, removed and changed leaf paths for one surface."""
    before = flatten(previous)
    after = flatten(current)
    changes: list[Change] = []
    for path in sorted(before.keys() | after.keys()):
        if path not in before:
            changes.append(Change(surface, path, "added", new=after[path]))
        elif path not in after:
            changes.append(Change(surface, path, "removed", old=before[path]))
        elif before[path] != after[path]:
            changes.append(Change(surface, path, "changed", old=before[path], new=after[path]))
    return changes


def next_minor(version: str) -> str:
    """The first patch of the next minor band, in SemVer form."""
    parsed = Version(version)
    return f"{parsed.major}.{parsed.minor + 1}.0"


def _field_leaves(flat: dict[str, Any], path: str) -> list[str]:
    """Every leaf belonging to a field, including the field's own scalar value."""
    return [leaf for leaf in flat if leaf == path or leaf.startswith(f"{path}.")]


def _is_warned(flat: dict[str, Any], path: str, warning: str) -> bool:
    for leaf in _field_leaves(flat, path):
        if leaf == path or leaf.rsplit(".", 1)[-1] not in WARNING_KEYS:
            continue
        value = flat[leaf]
        if value is True or (isinstance(value, str) and warning in value):
            return True
    return False


def _deprecation_key(record: dict[str, Any]) -> tuple[str, str]:
    return str(record.get("surface", "")), str(record.get("path", ""))


def _problem_list(changes: list[Change], heading: str) -> str:
    shown = "\n".join(f"    {change}" for change in changes[:25])
    remaining = len(changes) - min(len(changes), 25)
    if remaining:
        shown += f"\n    ... and {remaining} more"
    return f"{heading}\n{shown}"


def evaluate(
    current: dict[str, Any],
    previous: dict[str, Any],
    manifest: dict[str, Any] | str,
    deprecations: list[dict[str, Any]],
    version: str | None = None,
) -> list[str]:
    """Return every reason the current contract may not replace the snapshot.

    ``manifest`` is the committed manifest in production and a bare version string in the
    gate's own tests. ``version`` is the live API version; when omitted, the snapshot's own
    version is used so an unchanged contract always passes.
    """
    snapshot_version = manifest["version"] if isinstance(manifest, dict) else manifest
    current_version = version if version is not None else snapshot_version
    problems: list[str] = []

    for surface in SURFACES:
        changes = surface_diff(surface, current.get(surface, {}), previous.get(surface, {}))
        if not changes:
            continue
        if Version(current_version) == Version(snapshot_version):
            problems.append(
                _problem_list(
                    changes,
                    f"the {surface} contract changed without a version bump "
                    f"(still {current_version}); name the changed fields below, bump "
                    "ezchain_api.__version__, then regenerate the snapshots",
                )
            )
        else:
            problems.append(
                _problem_list(
                    changes,
                    f"the {surface} {STALE_SNAPSHOT_MESSAGE}: they record {snapshot_version} but the "
                    f"API is {current_version}; review the changed fields and regenerate "
                    "the snapshots with tools/check_contracts.py --update",
                )
            )

    live_leaves = {surface: flatten(current.get(surface, {})) for surface in SURFACES}
    by_key = {_deprecation_key(record): record for record in deprecations}

    for record in deprecations:
        surface, path = _deprecation_key(record)
        warning = str(record.get("warning", ""))
        try:
            deprecated_in = Version(str(record["deprecated_in"]))
            removed_in = Version(str(record["removed_in"]))
        except (KeyError, InvalidVersion):
            problems.append(f"deprecation for {surface}:{path} has an invalid version pair")
            continue
        if next_minor(str(deprecated_in)) != str(removed_in):
            problems.append(
                f"deprecation for {surface}:{path} must last exactly one minor version "
                f"({deprecated_in} -> {next_minor(str(deprecated_in))})"
            )
        if not warning:
            problems.append(f"deprecation for {surface}:{path} has no warning text")
        if deprecated_in <= Version(current_version) < removed_in:
            leaves = _field_leaves(live_leaves.get(surface, {}), path)
            if not leaves:
                problems.append(
                    f"deprecated field {surface}:{path} must still be present until {removed_in}"
                )
            elif not _is_warned(live_leaves[surface], path, warning):
                problems.append(
                    f"deprecated field {surface}:{path} must advertise its warning "
                    f"({warning!r}) while it still works"
                )

    for surface in SURFACES:
        removed = [
            change
            for change in surface_diff(surface, current.get(surface, {}), previous.get(surface, {}))
            if change.kind == "removed"
        ]
        for change in removed:
            for (record_surface, record_path), record in by_key.items():
                if record_surface != surface or not change.path.startswith(f"{record_path}."):
                    continue
                if Version(current_version) < Version(str(record.get("removed_in", "0"))):
                    problems.append(
                        f"{surface}: field {change.path} was removed before its deprecation "
                        f"ended at {record['removed_in']}"
                    )
                break
            else:
                problems.append(
                    f"{surface}: field {change.path} was removed without a deprecation record"
                )

    return problems
