"""Versioned plugin manifest (``ezchain.plugin/2``).

The manifest is data the host reads before it touches plugin code. Validation is
strict: unknown keys, unknown kinds and unknown capabilities fail closed so a typo
cannot silently widen a grant. This module never imports a plugin module.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, NoReturn

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from ..manifest import (
    MANIFEST_FILENAME,
    SDK_VERSION,
    ManifestError,
    check_compatible,
)
from ..manifest import (
    parse_manifest as parse_v1_manifest,
)
from ..params import SUPPORTED_KEYWORDS
from .capabilities import CAPABILITIES, KINDS, SCOPES, can_request

SCHEMA_ID = "ezchain.plugin/2"
MANIFEST_SCHEMA_VERSION = "2"
MAX_MANIFEST_BYTES = 256 * 1024

REQUIRED_FIELDS: tuple[str, ...] = (
    "schema",
    "id",
    "name",
    "version",
    "kind",
    "capabilities",
    "required_scopes",
    "compatibility",
    "config_schema",
    "license",
    "author",
    "source_hash",
)

OPTIONAL_FIELDS: tuple[str, ...] = (
    "description",
    "entrypoint",
    "chains",
    "venues",
    "data_scopes",
    "modes",
    "timeframes",
    "sdk",
)


PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
ENTRYPOINT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")
SOURCE_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

LEGACY_CAPABILITIES = MappingProxyType(
    {"read_market": "read_market", "read_account": "read_account", "submit_intent": "propose_order"}
)


def _fail(code: str, message: str, field: str = "") -> NoReturn:
    raise PluginManifestError(code, message, field=field)


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail("PLUGIN_MANIFEST_INVALID", f"{field} must be a non-empty string", field)
    return value.strip()


def _string_list(
    value: object,
    field: str,
    *,
    allowed: frozenset[str] | None = None,
    unknown_code: str = "PLUGIN_MANIFEST_INVALID",
    non_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        _fail("PLUGIN_MANIFEST_INVALID", f"{field} must be a list of strings", field)
    items = tuple(item.strip() for item in value)
    if any(not item for item in items):
        _fail("PLUGIN_MANIFEST_INVALID", f"{field} contains an empty string", field)
    if non_empty and not items:
        _fail("PLUGIN_MANIFEST_INVALID", f"{field} must not be empty", field)
    if len(items) != len(set(items)):
        _fail("PLUGIN_MANIFEST_INVALID", f"{field} contains duplicates", field)
    if allowed is not None:
        unknown = sorted(set(items) - allowed)
        if unknown:
            _fail(unknown_code, f"unknown {field}: {', '.join(unknown)}", field)
    return items


def _specifier(value: object, field: str) -> SpecifierSet:
    text = _string(value, field)
    try:
        return SpecifierSet(text)
    except InvalidSpecifier as exc:
        _fail("PLUGIN_MANIFEST_INVALID", f"{field} is not a version range", field)
        raise AssertionError from exc


def _check_schema_keywords(schema: object, field: str) -> None:
    if not isinstance(schema, Mapping):
        _fail("PLUGIN_MANIFEST_INVALID", f"{field} must be an object", field)
    unknown = sorted(set(schema) - SUPPORTED_KEYWORDS)
    if unknown:
        _fail(
            "PLUGIN_SCHEMA_UNSUPPORTED",
            f"unsupported schema keyword(s): {', '.join(unknown)}",
            field,
        )
    for key in ("properties",):
        children = schema.get(key)
        if isinstance(children, Mapping):
            for name, child in children.items():
                _check_schema_keywords(child, f"{field}.{key}.{name}")
    for key in ("anyOf", "oneOf", "allOf"):
        branches = schema.get(key)
        if isinstance(branches, list):
            for index, child in enumerate(branches):
                _check_schema_keywords(child, f"{field}.{key}[{index}]")
    for key in ("items", "additionalProperties"):
        child = schema.get(key)
        if isinstance(child, Mapping):
            _check_schema_keywords(child, f"{field}.{key}")


class PluginManifestError(ValueError):
    """A manifest the host refuses. ``code`` is stable and safe to expose."""

    def __init__(self, code: str, message: str, *, field: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "field": self.field}


@dataclass(frozen=True)
class Compatibility:
    """The manifest schema version and the product version range it targets."""

    manifest: str
    host: str
    sdk: str | None = None


@dataclass(frozen=True)
class PluginManifestV2:
    """A validated, immutable manifest record. JSON-safe via ``to_dict``."""

    schema: str
    id: str
    name: str
    version: str
    kind: str
    capabilities: tuple[str, ...]
    required_scopes: tuple[str, ...]
    compatibility: Compatibility
    config_schema: Mapping[str, Any]
    license: str
    author: str
    source_hash: str | None = None
    description: str = ""
    entrypoint: str | None = None
    chains: tuple[str, ...] = ()
    venues: tuple[str, ...] = ()
    data_scopes: tuple[str, ...] = ()
    modes: tuple[str, ...] = ("paper",)
    timeframes: tuple[str, ...] = ()
    sdk: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "kind": self.kind,
            "capabilities": list(self.capabilities),
            "required_scopes": list(self.required_scopes),
            "compatibility": {
                "manifest": self.compatibility.manifest,
                "host": self.compatibility.host,
            },
            "config_schema": dict(self.config_schema),
            "license": self.license,
            "author": self.author,
            "source_hash": self.source_hash,
            "description": self.description,
            "entrypoint": self.entrypoint,
            "chains": list(self.chains),
            "venues": list(self.venues),
            "data_scopes": list(self.data_scopes),
            "modes": list(self.modes),
            "timeframes": list(self.timeframes),
            "sdk": self.sdk,
        }


def parse_plugin_manifest(data: object, *, host_version: str | None = None) -> PluginManifestV2:
    """Validate a decoded v2 manifest document and return a typed record."""
    if not isinstance(data, Mapping):
        _fail("PLUGIN_MANIFEST_INVALID", "manifest must be a JSON object")
    schema = data.get("schema")
    if schema is None:
        _fail(
            "PLUGIN_MANIFEST_SCHEMA_UNSUPPORTED",
            "schema is required (legacy manifests need the loader)",
            "schema",
        )
    if schema != SCHEMA_ID:
        _fail(
            "PLUGIN_MANIFEST_SCHEMA_UNSUPPORTED",
            f"unsupported manifest schema: {schema}",
            "schema",
        )
    extensions = {key for key in data if isinstance(key, str) and key.startswith("x-")}
    unknown = sorted(set(data) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS) - extensions)
    if unknown:
        _fail(
            "PLUGIN_MANIFEST_UNKNOWN_KEY",
            f"unknown manifest key(s): {', '.join(unknown)}",
        )
    for field in REQUIRED_FIELDS:
        if field not in data:
            _fail("PLUGIN_MANIFEST_MISSING_FIELD", f"{field} is required", field)

    plugin_id = _string(data["id"], "id")
    if not PLUGIN_ID_RE.fullmatch(plugin_id):
        _fail(
            "PLUGIN_MANIFEST_INVALID",
            "id must be 3-128 lowercase letters, digits, '.', '_' or '-'",
            "id",
        )
    name = _string(data["name"], "name")
    version = _string(data["version"], "version")
    if not SEMVER_RE.fullmatch(version):
        _fail("PLUGIN_MANIFEST_INVALID", "version must be a semantic version", "version")
    try:
        Version(version)
    except InvalidVersion as exc:
        raise PluginManifestError(
            "PLUGIN_MANIFEST_INVALID", "version must be a semantic version", field="version"
        ) from exc

    kind = _string(data["kind"], "kind")
    if kind not in KINDS:
        _fail("PLUGIN_KIND_UNKNOWN", f"unknown plugin kind: {kind}", "kind")
    capabilities = _string_list(
        data["capabilities"],
        "capabilities",
        allowed=frozenset(CAPABILITIES),
        unknown_code="PLUGIN_CAPABILITY_UNKNOWN",
        non_empty=True,
    )
    for capability in capabilities:
        if not can_request(kind, capability):
            _fail(
                "PLUGIN_CAPABILITY_NOT_ALLOWED",
                f"kind {kind} cannot request {capability}",
                "capabilities",
            )
    required_scopes = _string_list(
        data["required_scopes"],
        "required_scopes",
        allowed=frozenset(SCOPES),
        unknown_code="PLUGIN_SCOPE_UNKNOWN",
    )

    compatibility_raw = data["compatibility"]
    if not isinstance(compatibility_raw, Mapping):
        _fail("PLUGIN_MANIFEST_INVALID", "compatibility must be an object", "compatibility")
    for field in ("manifest", "host"):
        if field not in compatibility_raw:
            _fail("PLUGIN_MANIFEST_MISSING_FIELD", f"compatibility.{field} is required", field)
    extra_compat = sorted(set(compatibility_raw) - {"manifest", "host"})
    if extra_compat:
        _fail(
            "PLUGIN_SCHEMA_UNSUPPORTED",
            f"unknown compatibility key(s): {', '.join(extra_compat)}",
            "compatibility",
        )
    manifest_version = _string(compatibility_raw["manifest"], "compatibility.manifest")
    if manifest_version != MANIFEST_SCHEMA_VERSION:
        _fail(
            "PLUGIN_MANIFEST_INVALID",
            f"compatibility.manifest must be {MANIFEST_SCHEMA_VERSION}",
            "compatibility.manifest",
        )
    host_range = _specifier(compatibility_raw["host"], "compatibility.host")
    if host_version is not None:
        try:
            current = Version(host_version)
        except InvalidVersion as exc:
            raise PluginManifestError(
                "PLUGIN_MANIFEST_INVALID", "host_version is not a version", field="host"
            ) from exc
        if not host_range.contains(current):
            _fail(
                "PLUGIN_HOST_INCOMPATIBLE",
                f"plugin requires host {compatibility_raw['host']}; host is {host_version}",
                "compatibility.host",
            )
    compatibility = Compatibility(
        manifest=manifest_version, host=str(compatibility_raw["host"]).strip()
    )

    sdk: str | None = None
    if data.get("sdk") is not None:
        sdk_range = _specifier(data["sdk"], "sdk")
        if not sdk_range.contains(Version(SDK_VERSION)):
            _fail(
                "PLUGIN_SDK_INCOMPATIBLE",
                f"plugin requires ezchain-sdk {data['sdk']}; host is {SDK_VERSION}",
                "sdk",
            )
        sdk = str(data["sdk"]).strip()

    config_schema = data["config_schema"]
    if not isinstance(config_schema, Mapping):
        _fail("PLUGIN_MANIFEST_INVALID", "config_schema must be an object", "config_schema")
    if config_schema.get("type") not in (None, "object"):
        _fail(
            "PLUGIN_MANIFEST_INVALID",
            "config_schema top-level type must be object",
            "config_schema",
        )
    _check_schema_keywords(config_schema, "config_schema")

    license_name = _string(data["license"], "license")
    author = _string(data["author"], "author")
    source_hash = _string(data["source_hash"], "source_hash")
    if not SOURCE_HASH_RE.fullmatch(source_hash):
        _fail(
            "PLUGIN_SOURCE_HASH_INVALID",
            "source_hash must be sha256:<64 lowercase hex characters>",
            "source_hash",
        )

    entrypoint: str | None = None
    if data.get("entrypoint") is not None:
        entrypoint = _string(data["entrypoint"], "entrypoint")
        if not ENTRYPOINT_RE.fullmatch(entrypoint):
            _fail(
                "PLUGIN_MANIFEST_INVALID",
                "entrypoint must look like 'module.path:Class'",
                "entrypoint",
            )

    return PluginManifestV2(
        schema=SCHEMA_ID,
        id=plugin_id,
        name=name,
        version=version,
        kind=kind,
        capabilities=capabilities,
        required_scopes=required_scopes,
        compatibility=compatibility,
        config_schema=dict(config_schema),
        license=license_name,
        author=author,
        source_hash=source_hash,
        description=str(data.get("description", "")),
        entrypoint=entrypoint,
        chains=_string_list(data.get("chains", []), "chains"),
        venues=_string_list(data.get("venues", []), "venues"),
        data_scopes=_string_list(data.get("data_scopes", []), "data_scopes"),
        modes=_string_list(data.get("modes", ["paper"]), "modes"),
        timeframes=_string_list(data.get("timeframes", []), "timeframes"),
        sdk=sdk,
    )


def parse_legacy_manifest(data: object, *, host_version: str | None = None) -> PluginManifestV2:
    """Map a v1 ``ezchain.plugin.json`` to a strategy v2 record, without import."""
    del host_version  # v1 has no product-version range; the SDK range is checked below.
    if not isinstance(data, Mapping):
        _fail("PLUGIN_MANIFEST_INVALID", "manifest must be a JSON object")
    try:
        legacy = parse_v1_manifest(data)
        check_compatible(legacy)
    except ManifestError as exc:
        _fail(exc.code, exc.message)
    capabilities = tuple(
        sorted(
            {
                LEGACY_CAPABILITIES[permission]
                for permission in legacy.permissions
                if permission in LEGACY_CAPABILITIES
            }
        )
    )
    return PluginManifestV2(
        schema="ezchain.plugin/1",
        id=legacy.id,
        name=legacy.name,
        version=legacy.version,
        kind="strategy",
        capabilities=capabilities,
        required_scopes=(),
        compatibility=Compatibility(manifest="1", host="*", sdk=legacy.sdk),
        config_schema=dict(legacy.params_schema),
        license=legacy.license or "",
        author=legacy.author or "",
        source_hash=None,
        description=legacy.description,
        entrypoint=legacy.entrypoint,
        chains=legacy.chains,
        venues=legacy.venues,
        data_scopes=legacy.data_scopes,
        modes=legacy.modes,
        timeframes=legacy.timeframes,
        sdk=legacy.sdk,
    )


def parse_decoded_manifest(data: object, *, host_version: str | None = None) -> PluginManifestV2:
    """Dispatch a decoded manifest by schema version; a missing schema means v1."""
    if isinstance(data, Mapping) and data.get("schema") is None:
        return parse_legacy_manifest(data, host_version=host_version)
    return parse_plugin_manifest(data, host_version=host_version)


def load_plugin_manifest(path: Path, *, host_version: str | None = None) -> PluginManifestV2:
    """Read and validate one ``ezchain.plugin.json`` file, without importing code."""
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


__all__ = [
    "MANIFEST_FILENAME",
    "MANIFEST_SCHEMA_VERSION",
    "MAX_MANIFEST_BYTES",
    "OPTIONAL_FIELDS",
    "REQUIRED_FIELDS",
    "SCHEMA_ID",
    "Compatibility",
    "PluginManifestError",
    "PluginManifestV2",
    "load_plugin_manifest",
    "parse_decoded_manifest",
    "parse_legacy_manifest",
    "parse_plugin_manifest",
]
