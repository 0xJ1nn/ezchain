"""One supervised command for the whole ezCHAIN unit.

The gateway is a lifecycle and observability boundary, not a place for business logic:
libraries run inside the unit, processes that need their own address space are started and
reaped by the supervisor, and money authority stays in its own process.
"""

from .config import GatewayConfig, load_config
from .model import (
    API_RESIDENT_COMPONENTS,
    COMPONENTS,
    PROCESS_COMPONENTS,
    ComponentState,
    GatewayStatus,
    build_status,
    process_alive,
)
from .probes import liveness, readiness
from .state import read_states, write_states
from .supervisor import Supervisor, commands_for, launch_process, terminate_process

__all__ = [
    "API_RESIDENT_COMPONENTS",
    "COMPONENTS",
    "PROCESS_COMPONENTS",
    "ComponentState",
    "GatewayConfig",
    "GatewayStatus",
    "Supervisor",
    "build_status",
    "commands_for",
    "launch_process",
    "liveness",
    "load_config",
    "process_alive",
    "read_states",
    "readiness",
    "terminate_process",
    "write_states",
]
