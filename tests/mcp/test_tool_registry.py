"""Tool declarations carry their cost and effect, and domains register by module.

A new tool domain must be addable by dropping a ``tools_*.py`` module into the package;
editing a central list is what this suite refuses. Every tool also declares whether it is
free or paid (and who bills it) and whether it reads, configures or trades.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ezchain_mcp import registry
from ezchain_mcp.registry import all_tools, discovered_tools, tool_by_name, write_tool_names
from ezchain_mcp.tools import READ_TOOLS, WRITE_TOOLS, Tool


def _handler(client: object, args: dict[str, object]) -> object:
    return {}


def _module_with_tools(name: str, *, read_only: bool = True) -> SimpleNamespace:
    tool = Tool(
        name=name,
        title="Probe tool",
        description="A tool declared by a discovered module.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=_handler,
        read_only=read_only,
    )
    return SimpleNamespace(TOOLS=(tool,))


def test_every_tool_declares_a_valid_cost_and_effect() -> None:
    for tool in all_tools(allow_writes=True):
        assert tool.cost in ("free", "paid"), tool.name
        assert tool.effect in ("read", "configure", "trade"), tool.name
        if tool.cost == "paid":
            assert tool.billed_provider, f"{tool.name} is paid but names no provider"


def test_read_tools_are_free_reads_and_mutations_are_not_reads() -> None:
    for tool in READ_TOOLS:
        assert tool.effect == "read", tool.name
        assert tool.cost == "free", tool.name
    for tool in WRITE_TOOLS:
        assert tool.effect in ("configure", "trade"), tool.name


def test_the_order_tool_is_declared_as_a_trade() -> None:
    order = tool_by_name(allow_writes=True)["ezchain_place_paper_order"]
    assert order.effect == "trade"
    assert order.cost == "free"


def test_a_paid_tool_must_name_the_provider_that_bills_it() -> None:
    with pytest.raises(ValueError):
        Tool(
            name="ezchain_paid",
            title="Paid",
            description="A paid tool.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=_handler,
            cost="paid",
            effect="read",
        )


def test_discovery_loads_tools_from_a_tools_underscore_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adding a module name is all it takes; the registry itself is not edited."""
    monkeypatch.setattr(registry, "_module_names", lambda: ("tools_probe",))
    monkeypatch.setattr(
        registry.importlib,
        "import_module",
        lambda name: _module_with_tools("ezchain_probe_tool"),
    )

    found = discovered_tools()

    assert [tool.name for tool in found] == ["ezchain_probe_tool"]


def test_discovered_tools_join_the_surface_without_editing_the_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "_module_names", lambda: ("tools_probe",))
    monkeypatch.setattr(
        registry.importlib,
        "import_module",
        lambda name: _module_with_tools("ezchain_configure_probe", read_only=False),
    )

    names = {tool.name for tool in all_tools(allow_writes=True)}
    assert "ezchain_configure_probe" in names
    assert "ezchain_configure_probe" in write_tool_names()

    read_only = {tool.name for tool in all_tools(allow_writes=False)}
    assert "ezchain_configure_probe" not in read_only


def test_tool_names_remain_unique_when_a_module_is_discovered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "_module_names", lambda: ("tools_probe",))
    monkeypatch.setattr(
        registry.importlib,
        "import_module",
        lambda name: _module_with_tools("ezchain_place_paper_order"),
    )

    with pytest.raises(ValueError, match="duplicate"):
        all_tools(allow_writes=True)
