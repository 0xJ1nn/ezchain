"""The read-only status model for the supervised unit.

Every component is reported, whether it runs in the gateway process or in its own address
space. The model never starts, stops or reaches into a component: it describes facts that
were collected elsewhere, and it says plainly when a fact could not be read.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from .config import GatewayConfig

# The complete unit, in the order an operator reads it.
COMPONENTS = ("api", "engine", "executor", "ingest", "agent-runtime")
# Libraries today: they run inside the unit and hold no separate address space.
IN_PROCESS_COMPONENTS = ("engine", "ingest")
# These need their own address space and are launched and reaped by the supervisor.
PROCESS_COMPONENTS = ("api", "executor", "agent-runtime")

KNOWN_STATES = frozenset({"starting", "running", "in_process", "stopped", "unreadable"})
UNREADABLE = "unreadable"

_MIGRATION_LABEL = "migration"


@dataclass(frozen=True)
class ComponentState:
    """One component's observable state at a point in time."""

    name: str
    status: str
    pid: int | None = None
    detail: str | None = None

    @property
    def readable(self) -> bool:
        return self.status != UNREADABLE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GatewayStatus:
    """A snapshot of the whole unit, safe to print or serialize verbatim."""

    components: tuple[ComponentState, ...]
    bind: str
    tls: str
    mode: str
    migration_head: str | None
    applied_revision: str | None
    ready: bool
    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """True only when every component's state could be read."""
        return all(component.readable for component in self.components)

    @property
    def tls_enabled(self) -> bool:
        return self.tls == "enabled"

    def lines(self) -> list[str]:
        summary = ", ".join(f"{component.name}={component.status}" for component in self.components)
        lines = [
            "ezchain gateway status",
            f"components: {summary}",
            f"bind: {self.bind}",
            f"tls: {self.tls}",
            f"mode: {self.mode}",
        ]
        if self.applied_revision is None or self.migration_head is None:
            lines.append(f"{_MIGRATION_LABEL}: unknown")
        elif self.applied_revision == self.migration_head:
            lines.append(f"{_MIGRATION_LABEL}: head ({self.applied_revision})")
        else:
            lines.append(
                f"{_MIGRATION_LABEL}: behind ({self.applied_revision} -> {self.migration_head})"
            )
        lines.extend(f"problem: {problem}" for problem in self.problems)
        lines.append(f"result: {'READY' if self.ready else 'NOT READY'}")
        return lines

    def to_dict(self) -> dict[str, Any]:
        return {
            "components": [component.to_dict() for component in self.components],
            "bind": self.bind,
            "tls": self.tls,
            "mode": self.mode,
            "migration_head": self.migration_head,
            "applied_revision": self.applied_revision,
            "ready": self.ready,
            "ok": self.ok,
            "problems": list(self.problems),
        }


def build_status(
    config: GatewayConfig,
    states: Sequence[ComponentState] | None = None,
    *,
    applied_revision: str | None = None,
    head_revision: str | None = None,
) -> GatewayStatus:
    """Assemble a snapshot from injected facts; the caller owns every probe."""
    if states is None:
        from .state import read_states

        states = read_states(config)

    components = tuple(states)
    problems: list[str] = []
    for component in components:
        if not component.readable:
            problems.append(f"{component.name}: state cannot be read")
        elif component.status == "starting":
            problems.append(f"{component.name}: still starting")

    if applied_revision is None or head_revision is None:
        problems.append("the migration head cannot be read")
    elif applied_revision != head_revision:
        problems.append(
            f"schema is behind the migration head ({applied_revision} -> {head_revision})"
        )

    ready = not problems and {component.name for component in components} == set(COMPONENTS)
    return GatewayStatus(
        components=components,
        bind=config.bind,
        tls="enabled" if config.tls else "disabled",
        mode=config.mode,
        migration_head=head_revision,
        applied_revision=applied_revision,
        ready=ready,
        problems=tuple(problems),
    )
