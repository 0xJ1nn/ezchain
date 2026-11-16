"""The tool registry: one declaration per tool, discovered by module.

A domain adds tools by dropping a ``tools_<domain>.py`` module into this package that
exports a ``TOOLS`` tuple. The server never keeps a hand-maintained list of domains, so a new
field can ship its own tools without editing this file or the core tool table.

The registry is the single place that decides which declarations form a surface. It refuses
duplicate names: two modules claiming one name is a wiring error, not a tool the model may
call with an ambiguous handler.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from . import tools as _core
from .tools import Tool

_SEARCH_PATH = [str(Path(__file__).resolve().parent)]


def _module_names() -> tuple[str, ...]:
    """The short names of every ``tools_*.py`` module in this package."""
    return tuple(
        name
        for _, name, is_package in pkgutil.iter_modules(_SEARCH_PATH)
        if name.startswith("tools_") and not is_package
    )


def discovered_tools() -> tuple[Tool, ...]:
    """Every tool declared by a discovered domain module, in module order."""
    found: list[Tool] = []
    for name in _module_names():
        module = importlib.import_module(f"{__package__}.{name}")
        found.extend(getattr(module, "TOOLS", ()))
    return tuple(found)


def all_tools(*, allow_writes: bool) -> tuple[Tool, ...]:
    """The full surface: core tools plus discovered domain tools."""
    discovered = discovered_tools()
    declarations = _core.READ_TOOLS + tuple(tool for tool in discovered if tool.read_only)
    if allow_writes:
        declarations = (
            declarations
            + _core.WRITE_TOOLS
            + tuple(tool for tool in discovered if not tool.read_only)
        )
    names = [tool.name for tool in declarations]
    if len(set(names)) != len(names):
        duplicates = sorted({name for name in names if names.count(name) > 1})
        raise ValueError(f"duplicate MCP tool declarations: {duplicates}")
    return declarations


def tool_by_name(*, allow_writes: bool) -> dict[str, Tool]:
    return {tool.name: tool for tool in all_tools(allow_writes=allow_writes)}


def write_tool_names() -> frozenset[str]:
    """Every declaration that mutates, including those from discovered modules.

    The server uses this to refuse a write by name even when the surface is read-only, so a
    hidden write answers ``WRITES_DISABLED`` rather than pretending the tool does not exist.
    """
    return frozenset(
        tool.name for tool in _core.WRITE_TOOLS + discovered_tools() if tool.effect != "read"
    )


__all__ = ["all_tools", "discovered_tools", "tool_by_name", "write_tool_names"]
