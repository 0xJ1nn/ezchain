"""Bring the unit up as one group and tear it down through the same supervisor.

The API process hosts the engine and the market scan: those two library components run
inside the API that the supervisor launches, not inside the gateway command. Components
that need their own address space — the API, the executor and the agent runtime — are
started and reaped by this supervisor. The executor remains a separate process: money
authority never moves into the gateway, and the gateway never imports it.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from .config import TOKEN_ENV, GatewayConfig, split_bind, validate_bind_security
from .model import API_RESIDENT_COMPONENTS, COMPONENTS, ComponentState
from .state import read_states, write_states

Launcher = Callable[[Sequence[str], Path, dict[str, str]], int]
Terminator = Callable[[int], None]

STOP_GRACE_SECONDS = 5.0
_POLL_SECONDS = 0.05


def child_environment(config: GatewayConfig) -> dict[str, str]:
    """The environment a component is started with, including the API token."""
    environment = dict(os.environ)
    if config.token:
        environment[TOKEN_ENV] = config.token
    return environment


def commands_for(config: GatewayConfig) -> dict[str, tuple[str, ...]]:
    """The process-backed components and the argv that starts each one."""
    host, port = split_bind(config.bind)
    api: list[str] = [
        sys.executable,
        "-m",
        "uvicorn",
        "ezchain_api.main:app",
        "--host",
        host,
        "--port",
        port,
    ]
    if config.tls and config.tls_cert and config.tls_key:
        # A TLS label without real files must never produce a plaintext listener.
        api += [
            "--ssl-certfile",
            str(config.tls_cert),
            "--ssl-keyfile",
            str(config.tls_key),
        ]
    return {
        "api": tuple(api),
        "executor": (sys.executable, "-m", "ezchain_api.gateway.resident", "ezchain_executor"),
        "agent-runtime": (
            sys.executable,
            "-m",
            "ezchain_api.gateway.resident",
            "ezchain_agent_runtime",
        ),
    }


def launch_process(command: Sequence[str], log_path: Path, environment: dict[str, str]) -> int:
    """Start one component with its stdout and stderr merged into the unit's log."""
    with log_path.open("ab") as stream:
        child = subprocess.Popen(  # noqa: S603 - argv is assembled by commands_for
            list(command),
            stdout=stream,
            stderr=subprocess.STDOUT,
            env=environment,
        )
    return int(child.pid)


def terminate_process(pid: int) -> None:
    """Ask one process to stop, then wait for it to disappear before returning."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + STOP_GRACE_SECONDS
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(_POLL_SECONDS)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class Supervisor:
    """Own the lifecycle of every component in the unit."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        launcher: Launcher = launch_process,
        terminator: Terminator = terminate_process,
    ) -> None:
        self.config = config
        self._launch = launcher
        self._terminate = terminator

    def start(self) -> tuple[ComponentState, ...]:
        """Create the data directory, start process-backed components and record state."""
        validate_bind_security(self.config)
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        self.config.save()
        commands = commands_for(self.config)
        environment = child_environment(self.config)
        states: list[ComponentState] = []
        for name in COMPONENTS:
            if name in API_RESIDENT_COMPONENTS:
                # Engine and ingest live inside the API process the supervisor launches.
                states.append(ComponentState(name=name, status="in_api"))
                continue
            try:
                pid = self._launch(commands[name], self.config.log_path, environment)
            except OSError:
                states.append(
                    ComponentState(name=name, status="stopped", detail="component did not start")
                )
            else:
                states.append(ComponentState(name=name, status="running", pid=pid))
        resolved = tuple(states)
        write_states(self.config, resolved)
        return resolved

    def stop(self) -> tuple[ComponentState, ...]:
        """Stop every process we started and publish the stopped snapshot."""
        current = read_states(self.config)
        for component in current:
            if component.status == "running" and component.pid is not None:
                self._terminate(component.pid)
        stopped = tuple(
            ComponentState(name=component.name, status="stopped", detail=component.detail)
            for component in current
        )
        write_states(self.config, stopped)
        return stopped
