"""Plugin manifest parsing and compatibility checks.

The manifest is the only declaration the host trusts before importing plugin code. It is
validated completely, including unknown keys, because a typo in ``permissions`` must not
silently become a wider grant.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

SDK_VERSION = "1.0.0"
MANIFEST_FILENAME = "ezchain.plugin.json"
MAX_MANIFEST_BYTES = 256 * 1024

PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
ENTRYPOINT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")

PERMISSIONS = frozenset({"read_market", "read_account", "submit_intent"})
MODES = frozenset({"paper", "live"})
TIMEFRAMES = frozenset({"1m", "5m", "15m", "1h", "4h", "1d"})

TOP_LEVEL_KEYS = frozenset(
    {
        "id",
        "name",
        "version",
        "sdk",
        "entrypoint",
        "description",
        "author",
        "license",
        "homepage",
        "chains",
        "venues",
        "capabilities_required",
        "data_scopes",
        "permissions",
        "params_schema",
        "primary_timeframe",
        "timeframes",
        "modes",
        "context_bars",
        "ui",
    }
)


class ManifestError(ValueError):
    """A manifest the host refuses to load. ``code`` is stable and API-safe."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _string_list(value: object, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ManifestError("PLUGIN_MANIFEST_INVALID", f"{key} must be a list of strings")
    cleaned = tuple(item.strip() for item in value)
    if any(not item for item in cleaned):
        raise ManifestError("PLUGIN_MANIFEST_INVALID", f"{key} contains an empty string")
    return cleaned


def _optional_string(value: object, key: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ManifestError("PLUGIN_MANIFEST_INVALID", f"{key} must be a string")
    return value.strip() or None


@dataclass(frozen=True)
class PluginManifest:
    """A validated plugin declaration. Immutable and JSON-safe."""

    id: str
    name: str
    version: str
    sdk: str
    entrypoint: str
    description: str = ""
    author: str | None = None
    license: str | None = None
    homepage: str | None = None
    chains: tuple[str, ...] = ()
    venues: tuple[str, ...] = ()
    capabilities_required: tuple[str, ...] = ()
    data_scopes: tuple[str, ...] = ()
    permissions: frozenset[str] = field(default_factory=frozenset)
    params_schema: dict[str, Any] = field(default_factory=dict)
    primary_timeframe: str | None = None
    timeframes: tuple[str, ...] = ()
    modes: tuple[str, ...] = ("paper",)
    context_bars: int = 200
    ui: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "sdk": self.sdk,
            "entrypoint": self.entrypoint,
            "description": self.description,
            "author": self.author,
            "license": self.license,
            "homepage": self.homepage,
            "chains": list(self.chains),
            "venues": list(self.venues),
            "capabilities_required": list(self.capabilities_required),
            "data_scopes": list(self.data_scopes),
            "permissions": sorted(self.permissions),
            "params_schema": self.params_schema,
            "primary_timeframe": self.primary_timeframe,
            "timeframes": list(self.timeframes),
            "modes": list(self.modes),
            "context_bars": self.context_bars,
            "ui": self.ui,
        }

    @property
    def is_paper_only(self) -> bool:
        return self.modes == ("paper",)


def parse_manifest(data: object) -> PluginManifest:
    """Validate a decoded manifest document. Raises :class:`ManifestError`."""
    if not isinstance(data, Mapping):
        raise ManifestError("PLUGIN_MANIFEST_INVALID", "manifest must be a JSON object")
    unknown = sorted(set(data) - TOP_LEVEL_KEYS - {key for key in data if key.startswith("x-")})
    if unknown:
        raise ManifestError(
            "PLUGIN_MANIFEST_UNKNOWN_KEY",
            f"unknown manifest key(s): {', '.join(unknown)}",
        )

    plugin_id = _optional_string(data.get("id"), "id")
    if plugin_id is None or not PLUGIN_ID_RE.fullmatch(plugin_id):
        raise ManifestError(
            "PLUGIN_MANIFEST_INVALID",
            "id must be 3-128 characters of lowercase letters, digits, '.', '_' or '-'",
        )
    name = _optional_string(data.get("name"), "name")
    if name is None:
        raise ManifestError("PLUGIN_MANIFEST_INVALID", "name is required")

    version = _optional_string(data.get("version"), "version")
    if version is None or not VERSION_RE.fullmatch(version):
        raise ManifestError("PLUGIN_MANIFEST_INVALID", "version must be a semantic version")

    sdk = _optional_string(data.get("sdk"), "sdk")
    if sdk is None:
        raise ManifestError("PLUGIN_MANIFEST_INVALID", "sdk is required")
    try:
        SpecifierSet(sdk)
    except InvalidSpecifier as exc:
        raise ManifestError(
            "PLUGIN_MANIFEST_INVALID", f"sdk is not a version range: {sdk}"
        ) from exc

    entrypoint = _optional_string(data.get("entrypoint"), "entrypoint")
    if entrypoint is None or not ENTRYPOINT_RE.fullmatch(entrypoint):
        raise ManifestError(
            "PLUGIN_MANIFEST_INVALID",
            "entrypoint must look like 'my_strategy.strategy:Momentum'",
        )

    permissions = _string_list(data.get("permissions"), "permissions")
    unknown_permissions = sorted(set(permissions) - PERMISSIONS)
    if unknown_permissions:
        raise ManifestError(
            "PLUGIN_MANIFEST_INVALID",
            f"unknown permission(s): {', '.join(unknown_permissions)}",
        )

    modes = _string_list(data.get("modes", ["paper"]), "modes") or ("paper",)
    unknown_modes = sorted(set(modes) - MODES)
    if unknown_modes:
        raise ManifestError(
            "PLUGIN_MANIFEST_INVALID",
            f"unknown mode(s): {', '.join(unknown_modes)}",
        )
    if "paper" not in modes:
        raise ManifestError(
            "PLUGIN_MANIFEST_INVALID",
            "every plugin must declare the paper mode; live-only plugins are refused",
        )

    timeframes = _string_list(data.get("timeframes"), "timeframes")
    unknown_timeframes = sorted(set(timeframes) - TIMEFRAMES)
    if unknown_timeframes:
        raise ManifestError(
            "PLUGIN_MANIFEST_INVALID",
            f"unknown timeframe(s): {', '.join(unknown_timeframes)}",
        )
    primary = _optional_string(data.get("primary_timeframe"), "primary_timeframe")
    if primary is not None and primary not in TIMEFRAMES:
        raise ManifestError("PLUGIN_MANIFEST_INVALID", f"unknown primary_timeframe: {primary}")
    if primary is not None and primary not in timeframes:
        timeframes = (*timeframes, primary)

    context_bars = data.get("context_bars", 200)
    if isinstance(context_bars, bool) or not isinstance(context_bars, int):
        raise ManifestError("PLUGIN_MANIFEST_INVALID", "context_bars must be an integer")
    if not 2 <= context_bars <= 1000:
        raise ManifestError("PLUGIN_MANIFEST_INVALID", "context_bars must be between 2 and 1000")

    schema = data.get("params_schema", {"type": "object", "properties": {}})
    if not isinstance(schema, dict):
        raise ManifestError("PLUGIN_MANIFEST_INVALID", "params_schema must be an object")

    ui = data.get("ui", {})
    if not isinstance(ui, dict):
        raise ManifestError("PLUGIN_MANIFEST_INVALID", "ui must be an object")

    return PluginManifest(
        id=plugin_id,
        name=name,
        version=version,
        sdk=sdk,
        entrypoint=entrypoint,
        description=_optional_string(data.get("description"), "description") or "",
        author=_optional_string(data.get("author"), "author"),
        license=_optional_string(data.get("license"), "license"),
        homepage=_optional_string(data.get("homepage"), "homepage"),
        chains=_string_list(data.get("chains"), "chains"),
        venues=_string_list(data.get("venues"), "venues"),
        capabilities_required=_string_list(
            data.get("capabilities_required"), "capabilities_required"
        ),
        data_scopes=_string_list(data.get("data_scopes"), "data_scopes"),
        permissions=frozenset(permissions),
        params_schema=schema,
        primary_timeframe=primary,
        timeframes=tuple(dict.fromkeys(timeframes)),
        modes=tuple(dict.fromkeys(modes)),
        context_bars=context_bars,
        ui=ui,
    )


def load_manifest(path: Path) -> PluginManifest:
    """Read and validate one ``ezchain.plugin.json`` file."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ManifestError("PLUGIN_MANIFEST_UNREADABLE", f"cannot read {path.name}") from exc
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ManifestError("PLUGIN_MANIFEST_TOO_LARGE", "manifest exceeds 256 KiB")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestError("PLUGIN_MANIFEST_INVALID", f"manifest is not JSON: {exc.msg}") from exc
    return parse_manifest(data)


def check_compatible(manifest: PluginManifest, sdk_version: str = SDK_VERSION) -> None:
    """Refuse a plugin whose declared SDK range does not include the host version."""
    try:
        current = Version(sdk_version)
    except InvalidVersion as exc:  # pragma: no cover - SDK_VERSION is a constant
        raise ManifestError("PLUGIN_SDK_INVALID", "host SDK version is invalid") from exc
    if not SpecifierSet(manifest.sdk).contains(current):
        raise ManifestError(
            "PLUGIN_SDK_INCOMPATIBLE",
            f"plugin {manifest.id} requires ezchain-sdk {manifest.sdk}; host is {sdk_version}",
        )


__all__ = [
    "MANIFEST_FILENAME",
    "MAX_MANIFEST_BYTES",
    "MODES",
    "PERMISSIONS",
    "SDK_VERSION",
    "TIMEFRAMES",
    "ManifestError",
    "PluginManifest",
    "check_compatible",
    "load_manifest",
    "parse_manifest",
]
