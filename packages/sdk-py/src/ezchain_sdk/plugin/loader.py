"""Discover plugin directories and validate their manifests without importing code.

Discovery is deliberately small: it reads bytes, decodes JSON and calls the manifest
validator. It never imports a plugin module, never starts a process and never opens a
socket. Every invalid plugin becomes a typed issue so one bad package cannot hide the
rest of the catalogue.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .manifest import (
    MANIFEST_FILENAME,
    MAX_MANIFEST_BYTES,
    PluginManifestError,
    PluginManifestV2,
    parse_decoded_manifest,
)


@dataclass(frozen=True)
class PluginIssue:
    """A candidate that was found and refused. Safe to show in a catalogue."""

    path: str
    code: str
    message: str
    field: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "code": self.code,
            "message": self.message,
            "field": self.field,
        }


@dataclass(frozen=True)
class PluginRecord:
    """A validated, discovered plugin. Contains data, never a loaded module."""

    manifest: PluginManifestV2
    root: Path
    manifest_path: Path
    manifest_sha256: str

    @property
    def plugin_id(self) -> str:
        return self.manifest.id

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.manifest.id,
            "name": self.manifest.name,
            "version": self.manifest.version,
            "schema": self.manifest.schema,
            "kind": self.manifest.kind,
            "capabilities": list(self.manifest.capabilities),
            "source_hash": self.manifest.source_hash,
            "root": str(self.root),
            "manifest_path": str(self.manifest_path),
            "manifest_sha256": self.manifest_sha256,
        }


@dataclass(frozen=True)
class DiscoveryResult:
    """The immutable outcome of one discovery pass."""

    records: tuple[PluginRecord, ...] = ()
    issues: tuple[PluginIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "records": [record.to_dict() for record in self.records],
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _candidates(base: Path, issues: list[PluginIssue]) -> list[tuple[Path, Path]]:
    """Manifest paths and their plugin roots, in deterministic order."""
    pairs: list[tuple[Path, Path]] = []
    if base.is_file():
        return [(base, base.parent)]
    root_manifest = base / MANIFEST_FILENAME
    if root_manifest.is_file():
        pairs.append((root_manifest, base))
    try:
        children = sorted(child for child in base.iterdir() if child.is_dir())
    except OSError as exc:
        issues.append(
            PluginIssue(str(base), "PLUGIN_PATH_UNREADABLE", f"cannot list {base.name}: {exc}")
        )
        return pairs
    for child in children:
        candidate = child / MANIFEST_FILENAME
        if candidate.is_file():
            pairs.append((candidate, child))
    if not pairs:
        nested = any(any(child.rglob(MANIFEST_FILENAME)) for child in children)
        if not nested:
            issues.append(
                PluginIssue(str(base), "PLUGIN_MANIFEST_MISSING", "no plugin manifest found")
            )
    return pairs


def _parse_one(path: Path, *, host_version: str | None = None) -> PluginManifestV2:
    """Read one manifest file; no plugin module is touched."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PluginManifestError("PLUGIN_MANIFEST_UNREADABLE", f"cannot read {path.name}") from exc
    if len(raw) > MAX_MANIFEST_BYTES:
        raise PluginManifestError("PLUGIN_MANIFEST_TOO_LARGE", "manifest exceeds 256 KiB")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PluginManifestError(
            "PLUGIN_MANIFEST_INVALID", f"manifest is not JSON: {exc.msg}"
        ) from exc
    return parse_decoded_manifest(data, host_version=host_version)


def discover_plugins(
    paths: str | Path | Iterable[str | Path], *, host_version: str | None = None
) -> DiscoveryResult:
    """Discover and validate plugins under ``paths``. Never import plugin code."""
    bases = [paths] if isinstance(paths, str | Path) else list(paths)
    records: list[PluginRecord] = []
    issues: list[PluginIssue] = []
    seen: set[str] = set()

    for raw_base in bases:
        base = Path(raw_base).expanduser()
        if not base.exists():
            issues.append(
                PluginIssue(str(base), "PLUGIN_PATH_MISSING", "plugin path does not exist")
            )
            continue
        for manifest_path, root in _candidates(base, issues):
            manifest: PluginManifestV2
            try:
                manifest = _parse_one(manifest_path, host_version=host_version)
            except PluginManifestError as exc:
                issues.append(PluginIssue(str(manifest_path), exc.code, exc.message, exc.field))
                continue
            except OSError:
                issues.append(
                    PluginIssue(
                        str(manifest_path),
                        "PLUGIN_MANIFEST_UNREADABLE",
                        "cannot read manifest",
                    )
                )
                continue
            if manifest.id in seen:
                issues.append(
                    PluginIssue(
                        str(manifest_path),
                        "PLUGIN_DUPLICATE_ID",
                        f"{manifest.id} is already provided by another plugin; first wins",
                        "id",
                    )
                )
                continue
            seen.add(manifest.id)
            digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            records.append(
                PluginRecord(
                    manifest=manifest,
                    root=root,
                    manifest_path=manifest_path,
                    manifest_sha256=digest,
                )
            )

    return DiscoveryResult(
        records=tuple(sorted(records, key=lambda record: record.plugin_id)),
        issues=tuple(issues),
    )


__all__ = [
    "DiscoveryResult",
    "PluginIssue",
    "PluginRecord",
    "discover_plugins",
]
