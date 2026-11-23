"""The component state file is the unit's read-only observability surface.

The supervisor writes it when it brings the unit up or down; the status command only reads
it. A missing, malformed or incomplete document is reported as unreadable component state
rather than as a stopped unit, so a broken observation can never look like a healthy one.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from .config import GatewayConfig
from .model import COMPONENTS, KNOWN_STATES, UNREADABLE, ComponentState


def write_states(config: GatewayConfig, states: Sequence[ComponentState]) -> None:
    """Persist the resolved component states under the unit's data directory."""
    config.data_dir.mkdir(parents=True, exist_ok=True)
    document: dict[str, Any] = {
        "components": [component.to_dict() for component in states],
        "bind": config.bind,
        "mode": config.mode,
    }
    config.state_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _unreadable_all(detail: str) -> tuple[ComponentState, ...]:
    return tuple(ComponentState(name=name, status=UNREADABLE, detail=detail) for name in COMPONENTS)


def _decode(row: object) -> ComponentState | None:
    if not isinstance(row, dict):
        return None
    name, status = row.get("name"), row.get("status")
    if not isinstance(name, str) or name not in COMPONENTS:
        return None
    if not isinstance(status, str) or status not in KNOWN_STATES:
        return None
    pid = row.get("pid")
    if pid is not None and (not isinstance(pid, int) or isinstance(pid, bool)):
        return None
    detail = row.get("detail")
    if detail is not None and not isinstance(detail, str):
        return None
    return ComponentState(name=name, status=status, pid=pid, detail=detail)


def read_states(config: GatewayConfig) -> tuple[ComponentState, ...]:
    """Read every component state, or say that none of them could be read."""
    try:
        document = json.loads(config.state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _unreadable_all("the component state file cannot be read")
    if not isinstance(document, dict):
        return _unreadable_all("the component state document is not an object")
    rows = document.get("components")
    if not isinstance(rows, list):
        return _unreadable_all("the component list is missing")

    decoded = [state for row in rows if (state := _decode(row)) is not None]
    by_name = {state.name: state for state in decoded}
    if set(by_name) != set(COMPONENTS):
        return _unreadable_all("one or more components are missing from the state file")
    return tuple(by_name[name] for name in COMPONENTS)
