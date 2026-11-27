"""Data-only capability registry for plugin manifests.

This module answers two questions without touching a plugin module: may a kind ask
for a capability, and what host surface that capability maps to. Nothing here is a
grant — the host computes effective capabilities later, at install and at call time.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

KINDS: tuple[str, ...] = (
    "agent_tool",
    "automation_template",
    "data_source",
    "evaluator",
    "report_exporter",
    "strategy",
    "ui_widget",
    "venue",
)

CAPABILITIES: tuple[str, ...] = (
    "propose_order",
    "read_account",
    "read_ledger",
    "read_market",
    "render_widget",
    "write_config",
)

SCOPES: tuple[str, ...] = ("config", "read", "trade")

# Maximum a kind may request. The host may narrow this at install, never widen it.
KIND_CAPABILITIES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "data_source": frozenset({"read_market"}),
        "venue": frozenset({"read_market"}),
        "strategy": frozenset({"read_market", "read_account", "propose_order"}),
        "evaluator": frozenset({"read_market", "read_account", "render_widget"}),
        "automation_template": frozenset({"read_market", "read_account", "read_ledger"}),
        "ui_widget": frozenset({"render_widget"}),
        "report_exporter": frozenset(
            {"read_market", "read_account", "read_ledger", "render_widget"}
        ),
        "agent_tool": frozenset(
            {"read_market", "read_account", "read_ledger", "render_widget", "write_config"}
        ),
    }
)

# Descriptive, host-owned metadata. These are strings, not callables or grants.
CAPABILITY_HOST_SURFACE: Mapping[str, str] = MappingProxyType(
    {
        "read_market": "ctx.candles_for / ctx.snapshot — typed, closed-bar host snapshots",
        "read_account": "ctx.account / ctx.positions — typed own-account snapshots",
        "read_ledger": "ctx.ledger_slice — typed ledger rows chosen by the host",
        "propose_order": "propose() — one Intent per tick; host validates, executor signs",
        "write_config": "write_config(namespace, patch) — plugin's own namespace only",
        "render_widget": "render(spec) — host-rendered spec id; JSON data only",
    }
)

CAPABILITY_SCOPE: Mapping[str, str] = MappingProxyType(
    {
        "read_market": "read",
        "read_account": "read",
        "read_ledger": "read",
        "render_widget": "read",
        "propose_order": "trade",
        "write_config": "config",
    }
)


def allowed_capabilities(kind: str) -> frozenset[str]:
    """The maximum capability set a kind may request; empty for an unknown kind."""
    return KIND_CAPABILITIES.get(kind, frozenset())


def can_request(kind: str, capability: str) -> bool:
    """Whether a manifest of ``kind`` may request ``capability``."""
    return capability in allowed_capabilities(kind)


def effective_capabilities(kind: str, requested: object) -> tuple[str, ...]:
    """The data-only intersection of a request with a kind's allowlist, sorted."""
    items = requested if isinstance(requested, (tuple, list, frozenset, set)) else ()
    allowed = allowed_capabilities(kind)
    return tuple(sorted(capability for capability in items if capability in allowed))


def host_surface(capability: str) -> str:
    """A description of what the host exposes for a capability."""
    try:
        return CAPABILITY_HOST_SURFACE[capability]
    except KeyError as exc:
        raise ValueError(f"unknown capability: {capability}") from exc


def host_scope(capability: str) -> str:
    """The scope the host associates with a capability (request metadata only)."""
    try:
        return CAPABILITY_SCOPE[capability]
    except KeyError as exc:
        raise ValueError(f"unknown capability: {capability}") from exc


__all__ = [
    "CAPABILITIES",
    "CAPABILITY_HOST_SURFACE",
    "CAPABILITY_SCOPE",
    "KINDS",
    "KIND_CAPABILITIES",
    "SCOPES",
    "allowed_capabilities",
    "can_request",
    "effective_capabilities",
    "host_scope",
    "host_surface",
]
