"""The gateway entry point: one command starts, stops and describes the whole unit.

This module is deliberately uninterested in money authority. The executor is supervised as
its own process and is never imported here; the only facts this command reads are the
configuration document, the component state file and the applied schema revision.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence

from .config import GatewayConfig, load_config
from .model import build_status
from .probes import liveness, readiness
from .supervisor import Supervisor, launch_process, terminate_process

MigrationProbe = Callable[[], str | None]


def _migration_facts() -> tuple[str | None, str | None]:
    """Read the applied revision and the head, reporting absence instead of a traceback."""
    from ..cli import _probe_applied_revision, _probe_head_revision
    from ..config import get_settings

    try:
        applied = _probe_applied_revision(get_settings().database_url)
    except Exception:  # noqa: BLE001 - a failed probe is an absence, never a leaked detail
        applied = None
    try:
        head = _probe_head_revision()
    except Exception:  # noqa: BLE001 - same: the status model reports the absence
        head = None
    return applied, head


def _resolve(
    applied_revision: MigrationProbe | None,
    head_revision: MigrationProbe | None,
) -> tuple[str | None, str | None]:
    if applied_revision is not None or head_revision is not None:
        applied = None if applied_revision is None else _safe_probe(applied_revision)
        head = None if head_revision is None else _safe_probe(head_revision)
        return applied, head
    return _migration_facts()


def _safe_probe(probe: MigrationProbe) -> str | None:
    try:
        return probe()
    except Exception:  # noqa: BLE001 - a probe failure is an unreadable fact
        return None


def run_start(config: GatewayConfig) -> int:
    """Bring the unit up and publish the state file the status command reads."""
    supervisor = Supervisor(config, launcher=launch_process, terminator=terminate_process)
    states = supervisor.start()
    print(f"gateway: started {len(states)} components")
    return 0 if all(state.readable for state in states) else 1


def run_stop(config: GatewayConfig) -> int:
    """Stop the unit through the same supervisor that started it."""
    supervisor = Supervisor(config, launcher=launch_process, terminator=terminate_process)
    states = supervisor.stop()
    print(f"gateway: stopped {len(states)} components")
    return 0 if all(state.readable for state in states) else 1


def run_status(config: GatewayConfig) -> int:
    """Print the read-only status model; fail when a component state cannot be read."""
    applied, head = _migration_facts()
    status = build_status(config, applied_revision=applied, head_revision=head)
    for line in status.lines():
        print(line)
    return 0 if status.ok else 1


def run_readyz(
    config: GatewayConfig,
    *,
    applied_revision: MigrationProbe | None = None,
    head_revision: MigrationProbe | None = None,
) -> int:
    """Print the readiness probe body; fail while the schema is behind the head."""
    applied, head = _resolve(applied_revision, head_revision)
    status = build_status(config, applied_revision=applied, head_revision=head)
    ready, body = readiness(status)
    print(json.dumps(body))
    return 0 if ready else 1


def run_healthz() -> int:
    """Print the liveness probe body."""
    print(json.dumps(liveness()))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ezchain-gateway",
        description="Run the ezCHAIN unit as one supervised command.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("start", "bring the full unit up"),
        ("stop", "bring the full unit down"),
        ("status", "report component state, bind address, TLS, mode and migration head"),
        ("readyz", "readiness probe: fail while the schema is behind the Alembic head"),
        ("healthz", "liveness probe: report that the unit is answering"),
        ("liveness", "alias for healthz"),
        ("readiness", "alias for readyz"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--config", default=None, help="path to the one config document")
        command.add_argument("--data-dir", default=None, help="directory for state and logs")
        command.add_argument("--bind", default=None, help="listener address (host:port)")
        command.add_argument(
            "--tls",
            action=argparse.BooleanOptionalAction,
            default=None,
            help="report TLS enabled or disabled for this unit",
        )
        command.add_argument("--mode", default=None, help="execution mode reported by status")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args: argparse.Namespace = build_parser().parse_args(argv)
    config = load_config(
        args.config,
        data_dir=args.data_dir,
        bind=args.bind,
        tls=args.tls,
        mode=args.mode,
    )
    if args.command == "start":
        return run_start(config)
    if args.command == "stop":
        return run_stop(config)
    if args.command == "status":
        return run_status(config)
    if args.command in {"readyz", "readiness"}:
        return run_readyz(config)
    if args.command in {"healthz", "liveness"}:
        return run_healthz()
    return 2  # pragma: no cover - argparse rejects unknown commands
