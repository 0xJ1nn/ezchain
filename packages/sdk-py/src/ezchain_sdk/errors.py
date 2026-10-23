"""Stable error codes shared by the SDK and the host."""

from __future__ import annotations

from .manifest import ManifestError
from .params import ParamsError


class PluginError(RuntimeError):
    """Base class for a plugin failure the host can report without a traceback."""

    code = "PLUGIN_ERROR"


class PluginLoadError(PluginError):
    """The manifest is valid but the entrypoint could not be imported."""

    code = "PLUGIN_LOAD_FAILED"


class PluginContractError(PluginError):
    """The plugin returned something the host refuses."""

    code = "PLUGIN_CONTRACT_VIOLATION"


__all__ = [
    "ManifestError",
    "ParamsError",
    "PluginContractError",
    "PluginError",
    "PluginLoadError",
]
