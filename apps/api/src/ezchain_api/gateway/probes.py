"""Liveness and readiness probes for the supervised unit.

Liveness is a process fact and never touches storage. Readiness is a storage fact: it fails
while the applied schema revision is behind the migration head, and it says so without
leaking a connection string or an exception.
"""

from __future__ import annotations

from typing import Any

from .model import GatewayStatus


def liveness() -> dict[str, str]:
    """The unit is answering; this probe deliberately does not inspect components."""
    return {"status": "ok"}


def readiness(status: GatewayStatus) -> tuple[bool, dict[str, Any]]:
    """Return the readiness flag and a small body suitable for a probe response."""
    applied, head = status.applied_revision, status.migration_head
    if applied is None or head is None:
        migration = "unknown"
    elif applied == head:
        migration = "head"
    else:
        migration = "pending"
    body: dict[str, Any] = {
        "status": "ready" if status.ready else "not_ready",
        "migration": migration,
        "revision": applied,
        "head": head,
        "components": {component.name: component.status for component in status.components},
    }
    return status.ready, body
