"""Deterministic capture of the three published contract surfaces.

The OpenAPI document is read from the FastAPI app that production builds. MCP tools are
read from the registry the server registers. SDK interfaces are read by introspection, so a
renamed plugin hook or a new required field shows up as a diff without anyone maintaining
a second list by hand.
"""

from __future__ import annotations

import inspect
from dataclasses import MISSING, fields, is_dataclass
from typing import Any

SURFACES = ("openapi", "mcp_tools", "sdk_interfaces")
MANIFEST_CONSTANTS = (
    "SDK_VERSION",
    "MANIFEST_FILENAME",
    "MAX_MANIFEST_BYTES",
    "TOP_LEVEL_KEYS",
    "PERMISSIONS",
    "MODES",
    "TIMEFRAMES",
)


def capture_openapi() -> dict[str, Any]:
    from ezchain_api.main import create_app

    return create_app().openapi()


def capture_mcp_tools() -> dict[str, Any]:
    from ezchain_mcp.tools import all_tools

    return {tool.name: tool.to_dict() for tool in all_tools(True)}


def _describe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, (set, frozenset, list, tuple)):
        return sorted((_describe(item) for item in value), key=repr)
    if isinstance(value, dict):
        return {
            str(key): _describe(item)
            for key, item in sorted(value.items(), key=lambda kv: str(kv[0]))
        }
    return repr(value)


def _type_name(annotation: Any) -> str:
    if isinstance(annotation, str):
        return annotation
    return getattr(annotation, "__name__", repr(annotation))


def _capture_class(obj: type) -> dict[str, Any]:
    info: dict[str, Any] = {
        "kind": "class",
        "bases": [base.__name__ for base in obj.__bases__ if base is not object],
    }
    if is_dataclass(obj):
        info["fields"] = {
            field.name: {
                "type": _type_name(field.type),
                "default": (
                    repr(field.default)
                    if field.default is not MISSING
                    else "factory"
                    if field.default_factory is not MISSING
                    else "required"
                ),
            }
            for field in fields(obj)
        }
    methods: dict[str, str] = {}
    for name, member in sorted(vars(obj).items()):
        if name.startswith("_"):
            continue
        if isinstance(member, property):
            methods[name] = "<property>"
            continue
        if not callable(member):
            continue
        try:
            methods[name] = str(inspect.signature(member))
        except (TypeError, ValueError):
            methods[name] = "<unavailable>"
    if methods:
        info["methods"] = methods
    return info


def capture_sdk_interfaces() -> dict[str, Any]:
    import ezchain_sdk
    from ezchain_sdk import manifest as sdk_manifest

    captured: dict[str, Any] = {}
    for name in sorted(ezchain_sdk.__all__):
        obj = getattr(ezchain_sdk, name)
        if inspect.isclass(obj):
            captured[name] = _capture_class(obj)
        elif inspect.isfunction(obj):
            captured[name] = {"kind": "function", "signature": str(inspect.signature(obj))}
        else:
            captured[name] = {"kind": "value", "value": _describe(obj)}
    for name in MANIFEST_CONSTANTS:
        captured[f"manifest.{name}"] = {
            "kind": "value",
            "value": _describe(getattr(sdk_manifest, name)),
        }
    return captured


def capture_all() -> dict[str, Any]:
    return {
        "openapi": capture_openapi(),
        "mcp_tools": capture_mcp_tools(),
        "sdk_interfaces": capture_sdk_interfaces(),
    }
