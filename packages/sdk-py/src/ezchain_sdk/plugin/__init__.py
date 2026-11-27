"""Typed plugin manifest, loader and capability registry (data only).

Importing this package never imports a plugin module. Discovery reads manifest files
and returns records or structured issues; execution, sandboxing and installation are
separate, later concerns.
"""

from __future__ import annotations

from .capabilities import (
    CAPABILITIES,
    CAPABILITY_HOST_SURFACE,
    CAPABILITY_SCOPE,
    KIND_CAPABILITIES,
    KINDS,
    SCOPES,
    allowed_capabilities,
    can_request,
    effective_capabilities,
    host_scope,
    host_surface,
)
from .loader import DiscoveryResult, PluginIssue, PluginRecord, discover_plugins
from .manifest import (
    MANIFEST_FILENAME,
    SCHEMA_ID,
    Compatibility,
    PluginManifestError,
    PluginManifestV2,
    load_plugin_manifest,
    parse_legacy_manifest,
    parse_plugin_manifest,
)

__all__ = [
    "CAPABILITIES",
    "CAPABILITY_HOST_SURFACE",
    "CAPABILITY_SCOPE",
    "KINDS",
    "KIND_CAPABILITIES",
    "MANIFEST_FILENAME",
    "SCOPES",
    "SCHEMA_ID",
    "Compatibility",
    "DiscoveryResult",
    "PluginIssue",
    "PluginManifestError",
    "PluginManifestV2",
    "PluginRecord",
    "allowed_capabilities",
    "can_request",
    "discover_plugins",
    "effective_capabilities",
    "host_scope",
    "host_surface",
    "load_plugin_manifest",
    "parse_legacy_manifest",
    "parse_plugin_manifest",
]
