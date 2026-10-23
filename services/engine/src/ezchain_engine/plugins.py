"""Plugin discovery and manifest registry.

Two sources, both explicit:

* an installed distribution that declares an ``ezchain.strategies`` entry point and ships
  ``ezchain.plugin.json``;
* a directory listed in ``EZCHAIN_PLUGIN_PATHS`` that contains either the manifest or
  immediate child packages with one.

Discovery never imports plugin code. The registry is a catalogue; the worker imports only
after the manifest, SDK range, permissions and parameters pass.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, distributions, entry_points
from pathlib import Path
from typing import Any

from ezchain_sdk import (
    MANIFEST_FILENAME,
    SDK_VERSION,
    ManifestError,
    PluginManifest,
    check_compatible,
    load_manifest,
    validate_params,
)

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "ezchain.strategies"


class PluginNotFound(LookupError):
    """No available plugin with this id."""

    code = "PLUGIN_NOT_FOUND"

    def __init__(self, plugin_id: str) -> None:
        super().__init__(plugin_id)
        self.plugin_id = plugin_id


class PluginUnavailable(RuntimeError):
    """A plugin id exists in the catalogue but cannot run."""

    code = "PLUGIN_UNAVAILABLE"

    def __init__(self, plugin_id: str, reason: str) -> None:
        super().__init__(reason)
        self.plugin_id = plugin_id
        self.reason = reason


@dataclass(frozen=True)
class PluginIssue:
    """A manifest that was found and refused. Visible in the catalogue, never loaded."""

    source: str
    path: str | None
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "path": self.path,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True)
class PluginRecord:
    manifest: PluginManifest
    source: str
    root: Path | None
    manifest_path: Path | None
    manifest_sha256: str
    dist_name: str | None = None
    dist_version: str | None = None

    @property
    def plugin_id(self) -> str:
        return self.manifest.id

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.manifest.to_dict(),
            "status": "available",
            "source": self.source,
            "root": None if self.root is None else str(self.root),
            "manifest_sha256": self.manifest_sha256,
            "dist_name": self.dist_name,
            "dist_version": self.dist_version,
        }


@dataclass
class PluginRegistry:
    """A reloadable, immutable-per-generation catalogue."""

    paths: Sequence[str | Path] = field(default_factory=tuple)
    include_entry_points: bool = True
    _records: dict[str, PluginRecord] = field(default_factory=dict, init=False)
    _issues: list[PluginIssue] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.reload()

    def reload(self) -> None:
        records: dict[str, PluginRecord] = {}
        issues: list[PluginIssue] = []
        for path in self.paths:
            self._load_path(Path(path).expanduser(), records, issues)
        if self.include_entry_points:
            self._load_entry_points(records, issues)
        self._records = records
        self._issues = issues

    def _add(
        self,
        record: PluginRecord,
        records: dict[str, PluginRecord],
        issues: list[PluginIssue],
    ) -> None:
        existing = records.get(record.plugin_id)
        if existing is not None:
            issues.append(
                PluginIssue(
                    source=record.source,
                    path=None if record.manifest_path is None else str(record.manifest_path),
                    code="PLUGIN_DUPLICATE_ID",
                    message=(
                        f"{record.plugin_id} is already provided by {existing.source}; "
                        "the first discovery wins"
                    ),
                )
            )
            return
        records[record.plugin_id] = record

    def _record_for(
        self,
        manifest_path: Path,
        *,
        source: str,
        root: Path | None,
        dist_name: str | None = None,
        dist_version: str | None = None,
    ) -> PluginRecord:
        manifest = load_manifest(manifest_path)
        check_compatible(manifest)
        digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        return PluginRecord(
            manifest=manifest,
            source=source,
            root=root,
            manifest_path=manifest_path,
            manifest_sha256=digest,
            dist_name=dist_name,
            dist_version=dist_version,
        )

    def _load_path(
        self,
        path: Path,
        records: dict[str, PluginRecord],
        issues: list[PluginIssue],
    ) -> None:
        if not path.exists():
            issues.append(
                PluginIssue(
                    source="path",
                    path=str(path),
                    code="PLUGIN_PATH_MISSING",
                    message="plugin path does not exist",
                )
            )
            return
        candidates: list[Path] = []
        if path.is_file():
            candidates.append(path)
        else:
            manifest = path / MANIFEST_FILENAME
            if manifest.is_file():
                candidates.append(manifest)
            try:
                children = sorted(path.iterdir())
            except OSError as exc:
                issues.append(
                    PluginIssue(
                        source="path",
                        path=str(path),
                        code="PLUGIN_PATH_UNREADABLE",
                        message=str(exc),
                    )
                )
                return
            for child in children:
                if child.is_dir() and (child / MANIFEST_FILENAME).is_file():
                    candidates.append(child / MANIFEST_FILENAME)
        for manifest_path in candidates:
            try:
                root = manifest_path.parent if manifest_path.name == MANIFEST_FILENAME else None
                record = self._record_for(
                    manifest_path,
                    source="path",
                    root=root,
                )
            except ManifestError as exc:
                issues.append(
                    PluginIssue(
                        source="path",
                        path=str(manifest_path),
                        code=exc.code,
                        message=exc.message,
                    )
                )
                continue
            except OSError as exc:
                issues.append(
                    PluginIssue(
                        source="path",
                        path=str(manifest_path),
                        code="PLUGIN_MANIFEST_UNREADABLE",
                        message=str(exc),
                    )
                )
                continue
            self._add(record, records, issues)

    def _load_entry_points(
        self,
        records: dict[str, PluginRecord],
        issues: list[PluginIssue],
    ) -> None:
        for entry in self._entry_points():
            try:
                manifest_path = self._entry_manifest_path(entry)
                if manifest_path is None:
                    raise ManifestError(
                        "PLUGIN_MANIFEST_MISSING",
                        "installed distribution has no ezchain.plugin.json",
                    )
                dist = entry.dist
                record = self._record_for(
                    manifest_path,
                    source="entry_point",
                    root=None,
                    dist_name=None if dist is None else dist.metadata["Name"],
                    dist_version=None if dist is None else dist.version,
                )
            except ManifestError as exc:
                issues.append(
                    PluginIssue(
                        source="entry_point",
                        path=entry.value,
                        code=exc.code,
                        message=exc.message,
                    )
                )
                continue
            self._add(record, records, issues)

    def _entry_points(self) -> Iterable[EntryPoint]:
        try:
            return entry_points(group=ENTRY_POINT_GROUP)
        except TypeError:  # pragma: no cover - Python < 3.10 compatibility path
            return entry_points().get(ENTRY_POINT_GROUP, ())

    def _entry_manifest_path(self, entry: EntryPoint) -> Path | None:
        dist = entry.dist
        if dist is None:
            # Fall back to the module's package directory when metadata is unavailable.
            module = entry.module.split(".")[0]
            for candidate in distributions():
                if candidate.metadata["Name"] == module:
                    return self._manifest_in_distribution(candidate)
            return None
        return self._manifest_in_distribution(dist)

    @staticmethod
    def _manifest_in_distribution(dist: Any) -> Path | None:
        """Find the manifest wherever the wheel put it (root or package data)."""
        direct = Path(str(dist.locate_file(MANIFEST_FILENAME)))
        if direct.is_file():
            return direct
        for file in dist.files or ():
            if str(file).endswith(MANIFEST_FILENAME):
                candidate = Path(str(dist.locate_file(file)))
                if candidate.is_file():
                    return candidate
        return None

    @property
    def issues(self) -> tuple[PluginIssue, ...]:
        return tuple(self._issues)

    def list(self) -> list[PluginRecord]:
        return sorted(self._records.values(), key=lambda record: record.manifest.name.lower())

    def get(self, plugin_id: str) -> PluginRecord:
        record = self._records.get(plugin_id)
        if record is None:
            raise PluginNotFound(plugin_id)
        return record

    def require(self, plugin_id: str) -> PluginRecord:
        return self.get(plugin_id)

    def validate_config(self, plugin_id: str, config: Any) -> dict[str, Any]:
        """Validate user config against the installed manifest. Raises ``ParamsError``."""
        record = self.require(plugin_id)
        return validate_params(record.manifest.params_schema, config)

    def catalog(self) -> dict[str, Any]:
        return {
            "plugins": [record.to_dict() for record in self.list()],
            "issues": [issue.to_dict() for issue in self.issues],
            "sdk_version": SDK_VERSION,
        }


__all__ = [
    "ENTRY_POINT_GROUP",
    "PluginIssue",
    "PluginNotFound",
    "PluginRecord",
    "PluginRegistry",
    "PluginUnavailable",
]
