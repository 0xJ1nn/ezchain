"""Re-drill the Compose stack at the migration head of *this* checkout.

``python -m tools.ops drill`` is the runnable half of the Compose drill: it starts
``infra/compose.yaml`` under an isolated project name, waits until ``/readyz`` reports the
repository's own Alembic head, proves the one-shot ``migrate`` job exited 0, places one
paper buy through the edge, restarts the API and requires the ledger to be unchanged,
dumps the database and restores it into a scratch database, then removes the project.

The phases run in this order and a failure stops the drill at that phase:

    static     compose structure + migration chain integrity (no Docker needed)
    preflight  Docker CLI, daemon, compose config, ``infra/.env`` secrets
    up         ``docker compose up --build -d``
    ready      ``/readyz`` reports ``migration: head`` at this checkout's revision
    migrate    the one-shot ``migrate`` container exited 0
    paper      one paper buy through the edge + reconciliation ``ok``
    restart    ``restart api`` then identical cash and positions
    backup     dump, restore into a scratch database, same ledger row count
    teardown   ``down -v`` (skipped with ``--keep``)

Exit codes are part of the contract, like the rest of ``tools.ops``:

* ``0`` — every phase the drill ran passed;
* ``1`` — a phase failed; the drill still tears the project down;
* ``2`` — the drill refused to start: no Docker CLI, no daemon, unreadable compose file,
  missing secrets, or a ``compose config`` rejection.

A host without Docker gets ``2`` and a summary that says the container run was *not*
performed. That is a refusal, never a pass: ``--static-only`` is the honest mode for such
a host, and it is what the committed evidence used.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from alembic.script import ScriptDirectory

from ezchain_api.db import alembic_config

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2

TOOL = "tools.ops.drill"
FORMAT_VERSION = 1

DEFAULT_PROJECT = "ezchain-verify"
# A chain whose ladder is the capped DexScreener mark: the only quote path the stack can
# price without a venue credential or a keyed provider. `--chain/--token` override it.
DEFAULT_CHAIN = "aptos"
DEFAULT_TOKEN = "0x1::aptos_coin::AptosCoin"
SCRATCH_DATABASE = "ezchain_scratch_drill"

REQUIRED_SERVICES = ("postgres", "migrate", "api", "web", "caddy")
LONG_RUNNING = ("postgres", "api", "web", "caddy")
ENV_PLACEHOLDERS = frozenset({"", "replace-with-a-long-random-password", "changeme"})
MIN_DATABASE_PASSWORD = 16
MIN_API_TOKEN = 32


class Refused(RuntimeError):
    """A precondition the drill will not work around."""


class DrillFailure(RuntimeError):
    """A phase that ran and did not pass."""


@dataclass(frozen=True)
class Phase:
    name: str
    status: str
    seconds: float
    detail: str = ""

    def document(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "seconds": round(self.seconds, 3),
            "detail": self.detail,
        }


@dataclass
class DrillResult:
    status: str
    phases: list[Phase] = field(default_factory=list)
    document: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "passed"

    @property
    def exit_code(self) -> int:
        if self.ok:
            return EXIT_OK
        return EXIT_REFUSED if self.status == "skipped" else EXIT_FAILED


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _tail(data: bytes, limit: int = 400) -> str:
    text = data.decode("utf-8", "replace").strip()
    return text if len(text) <= limit else "…" + text[-limit:]


def read_env_file(path: Path) -> dict[str, str]:
    """Read a Compose env file as plain key/value pairs. Comments and blanks are skipped."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def env_problems(values: Mapping[str, str]) -> list[str]:
    """What makes ``infra/.env`` unusable for a drill. Names keys, never values."""
    problems: list[str] = []
    password = values.get("POSTGRES_PASSWORD", "")
    if "POSTGRES_PASSWORD" not in values:
        problems.append("POSTGRES_PASSWORD is missing")
    elif password in ENV_PLACEHOLDERS or len(password) < MIN_DATABASE_PASSWORD:
        problems.append(
            f"POSTGRES_PASSWORD is a placeholder or shorter than {MIN_DATABASE_PASSWORD} characters"
        )
    token = values.get("EZCHAIN_API_TOKEN", "")
    if "EZCHAIN_API_TOKEN" not in values:
        problems.append("EZCHAIN_API_TOKEN is missing")
    elif token in ENV_PLACEHOLDERS or len(token) < MIN_API_TOKEN:
        problems.append(f"EZCHAIN_API_TOKEN must be at least {MIN_API_TOKEN} characters")
    origin = values.get("EZCHAIN_PUBLIC_URL", "")
    if "EZCHAIN_PUBLIC_URL" not in values:
        problems.append("EZCHAIN_PUBLIC_URL is missing")
    elif not origin.startswith(("http://", "https://")):
        problems.append("EZCHAIN_PUBLIC_URL must be an http:// or https:// origin")
    return problems


def compose_problems(document: Mapping[str, Any]) -> list[str]:
    """Structural checks on ``infra/compose.yaml`` that do not need a daemon.

    These are the properties a drill depends on: the services exist, the ones that are
    supposed to stay up have a health signal, only the edge publishes a host port, the
    schema is applied by a one-shot job before the API starts, and Postgres is off the
    browser-facing network.
    """
    problems: list[str] = []
    services = document.get("services")
    if not isinstance(services, Mapping):
        return ["compose document has no services mapping"]

    missing = [name for name in REQUIRED_SERVICES if name not in services]
    if missing:
        problems.append(f"missing service(s): {', '.join(missing)}")

    for name in LONG_RUNNING:
        service = services.get(name)
        if not isinstance(service, Mapping):
            continue
        health = service.get("healthcheck")
        if not isinstance(health, Mapping) or not health.get("test"):
            problems.append(f"{name} has no healthcheck test")

    for name, service in services.items():
        if not isinstance(service, Mapping):
            continue
        if service.get("profiles"):
            continue
        if name != "caddy" and service.get("ports"):
            problems.append(f"{name} publishes a host port; only caddy may")

    migrate = services.get("migrate")
    if isinstance(migrate, Mapping):
        command = migrate.get("command")
        rendered = " ".join(str(item) for item in command) if isinstance(command, list) else ""
        if "upgrade" not in rendered or "head" not in rendered:
            problems.append("migrate does not run 'alembic upgrade head'")
        if migrate.get("restart") not in (None, "no"):
            problems.append(f"migrate restarts ({migrate.get('restart')!r}); a one-shot must not")

    api = services.get("api")
    if isinstance(api, Mapping):
        depends = api.get("depends_on")
        migrate_dep = depends.get("migrate") if isinstance(depends, Mapping) else None
        condition = migrate_dep.get("condition") if isinstance(migrate_dep, Mapping) else None
        if condition != "service_completed_successfully":
            problems.append("api does not wait for migrate to complete successfully")

    networks = document.get("networks")
    data = networks.get("data") if isinstance(networks, Mapping) else None
    if not isinstance(data, Mapping) or data.get("internal") is not True:
        problems.append("network 'data' is not internal")
    postgres = services.get("postgres")
    if isinstance(postgres, Mapping):
        attached = postgres.get("networks")
        names = attached if isinstance(attached, list) else list(attached or [])
        if names != ["data"]:
            problems.append(f"postgres is attached to {names!r}, expected only 'data'")

    return problems


def checkout_script_location(root: Path) -> Path | None:
    """The ``apps/api/alembic`` directory of ``root``, when this checkout has one.

    The head a drill must reach is the head of *this checkout*, so it is resolved from the
    compose file's root rather than from wherever the installed package happens to live.
    """
    candidate = root / "apps" / "api" / "alembic"
    return candidate if (candidate / "versions").is_dir() else None


def migration_chain(script_location: Path | None = None) -> tuple[str, int, list[str]]:
    """Return ``(head, revision_count, problems)`` for the Alembic chain.

    ``script_location`` is a directory holding a ``versions/`` subdirectory, exactly what
    Alembic's own setting means; the default is whatever ``alembic_config`` resolves.
    ``Drill._static`` passes the checkout's own directory, so "head" cannot silently mean
    another tree's head.

    A single head is required: two heads mean a merge is missing and ``upgrade head``
    would refuse or, worse, pick one. The chain is walked from the scripts rather than
    from a database, so this check runs on a host with no Postgres at all.
    """
    config = alembic_config()
    if script_location is not None:
        config.set_main_option("script_location", str(script_location))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    revisions = list(script.walk_revisions())
    problems: list[str] = []
    if not heads:
        return "", 0, ["the migration directory has no head revision"]
    if len(heads) > 1:
        problems.append(f"{len(heads)} heads ({', '.join(sorted(heads))}); a merge is missing")

    children: dict[str, int] = {}
    for revision in revisions:
        downward = revision.down_revision
        parents = tuple(downward) if isinstance(downward, tuple) else (downward,)
        for parent in parents:
            if parent:
                children[parent] = children.get(parent, 0) + 1
    for parent, count in sorted(children.items()):
        if count > 1:
            problems.append(f"{parent} is a branch point ({count} children)")
    return heads[0], len(revisions), problems


def parse_ps(raw: bytes) -> list[dict[str, Any]]:
    """Parse ``docker compose ps --format json`` in both array and line-delimited form."""
    text = raw.decode("utf-8", "replace").strip()
    if not text:
        return []
    try:
        parsed: Any = json.loads(text)
    except json.JSONDecodeError:
        parsed = [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(parsed, Mapping):
        parsed = [parsed]
    if not isinstance(parsed, list):
        raise DrillFailure(f"compose ps returned an unexpected document: {text[:200]}")
    return [row for row in parsed if isinstance(row, dict)]


class Drill:
    """One drill run. Nothing is claimed that a phase did not observe."""

    def __init__(
        self,
        *,
        root: Path,
        compose: Path,
        env_file: Path,
        project: str = DEFAULT_PROJECT,
        base_url: str | None = None,
        docker: str | None = None,
        timeout: float = 180.0,
        build_timeout: float = 1800.0,
        dest: Path | None = None,
        keep: bool = False,
        static_only: bool = False,
        chain: str = DEFAULT_CHAIN,
        token: str = DEFAULT_TOKEN,
    ) -> None:
        self.root = root
        self.compose = compose
        self.env_file = env_file
        self.project = project
        self.base_url = base_url.rstrip("/") if base_url else None
        self.docker_bin = docker or os.environ.get("EZCHAIN_DOCKER_BIN") or "docker"
        self.timeout = timeout
        self.build_timeout = build_timeout
        self.dest = dest
        self.keep = keep
        self.static_only = static_only
        self.chain = chain
        self.token = token

        self.phases: list[Phase] = []
        self.commands: list[list[str]] = []
        self.head: str | None = None
        self.docker_version: str | None = None
        self.compose_version: str | None = None
        self.artifacts: dict[str, Any] = {}
        self.paper: dict[str, Any] = {}
        self.failure: str | None = None
        self.refusal: str | None = None
        self._attempted_stack = False
        self._started = _now()

    # ------------------------------------------------------------------ plumbing

    def _run(
        self,
        command: Sequence[str],
        *,
        stdin: bytes | None = None,
        timeout: float = 120.0,
    ) -> subprocess.CompletedProcess[bytes]:
        self.commands.append(list(command))
        return subprocess.run(
            list(command), input=stdin, capture_output=True, timeout=timeout, check=False
        )

    def _compose(
        self,
        *args: str,
        stdin: bytes | None = None,
        timeout: float = 120.0,
    ) -> subprocess.CompletedProcess[bytes]:
        command = [self.docker_bin, "compose", "-p", self.project, "-f", str(self.compose)]
        if self.env_file.is_file():
            command += ["--env-file", str(self.env_file)]
        command += list(args)
        return self._run(command, stdin=stdin, timeout=timeout)

    def _http(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout: float = 15.0,
    ) -> tuple[int, Any]:
        if self.base_url is None:  # pragma: no cover - only a static-only run skips this
            raise DrillFailure("no base URL was resolved")
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method)
        if data is not None:
            request.add_header("content-type", "application/json")
        # The Next proxy refuses a cross-origin Origin; naming the edge is what a browser
        # does, and curl without it is a different code path than the one under test.
        request.add_header("origin", self.base_url)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            body = error.read()
            try:
                return error.code, json.loads(body or b"null")
            except json.JSONDecodeError:
                return error.code, body.decode("utf-8", "replace")[:400]

    def _phase(self, name: str, action: Any) -> None:
        started = time.monotonic()
        try:
            detail = action() or ""
        except Refused as exc:
            self.refusal = str(exc)
            self.phases.append(Phase(name, "skipped", time.monotonic() - started, str(exc)))
            raise
        except DrillFailure as exc:
            self.failure = str(exc)
            self.phases.append(Phase(name, "failed", time.monotonic() - started, str(exc)))
            raise
        except Exception as exc:  # noqa: BLE001 - an unexpected error must not read as a pass
            message = f"{type(exc).__name__}: {exc}"
            self.failure = message
            self.phases.append(Phase(name, "failed", time.monotonic() - started, message))
            raise DrillFailure(message) from exc
        self.phases.append(Phase(name, "passed", time.monotonic() - started, detail))

    def _poll_ready(self) -> str:
        """Wait for ``/readyz`` to report ready at this checkout's head."""
        assert self.head is not None
        interval = max(0.05, min(2.0, self.timeout / 5))
        deadline = time.monotonic() + self.timeout
        last = "no response yet"
        while True:
            try:
                status, body = self._http("GET", "/readyz", timeout=5.0)
            except OSError as exc:
                last = f"{type(exc).__name__}: {exc}"
            else:
                last = f"HTTP {status} {json.dumps(body, sort_keys=True)[:300]}"
                if (
                    status == 200
                    and isinstance(body, Mapping)
                    and body.get("status") == "ready"
                    and body.get("migration") == "head"
                    and body.get("revision") == self.head
                ):
                    return f"ready at {self.head}"
            if time.monotonic() >= deadline:
                break
            time.sleep(interval)
        raise DrillFailure(
            f"{self.base_url}/readyz did not report head {self.head} within "
            f"{self.timeout:.0f}s: {last}"
        )

    # -------------------------------------------------------------------- phases

    def _static(self) -> str:
        if not self.compose.is_file():
            raise Refused(f"compose file not found: {self.compose}")
        try:
            document = yaml.safe_load(self.compose.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise Refused(f"compose file is not valid YAML: {exc}") from None
        if not isinstance(document, Mapping):
            raise Refused("compose file is not a mapping")
        problems = compose_problems(document)
        script_location = checkout_script_location(self.root)
        head, count, chain_problems = migration_chain(script_location)
        problems += chain_problems
        if problems:
            raise DrillFailure("; ".join(problems))
        self.head = head
        services = document.get("services") or {}
        return f"compose valid ({len(services)} services); {count} migrations, head {head}"

    def _preflight(self) -> str:
        binary = shutil.which(self.docker_bin)
        if binary is None:
            raise Refused(
                f"{self.docker_bin} is not on PATH: install Docker (or Podman with a compose "
                "plugin), or run `python -m tools.ops drill --static-only`"
            )
        self.docker_bin = binary
        info = self._run([binary, "info", "--format", "{{.ServerVersion}}"], timeout=30)
        if info.returncode != 0:
            raise Refused(
                f"no Docker daemon reachable: {_tail(info.stderr) or 'docker info failed'}"
            )
        self.docker_version = info.stdout.decode("utf-8", "replace").strip()

        version = self._run([binary, "compose", "version"], timeout=30)
        if version.returncode != 0:
            raise Refused(f"docker compose is unavailable: {_tail(version.stderr)}")
        self.compose_version = _tail(version.stdout, 80)

        if not self.env_file.is_file():
            raise Refused(
                f"{self.env_file} is missing: copy infra/.env.example and set "
                "POSTGRES_PASSWORD and EZCHAIN_API_TOKEN"
            )
        values = read_env_file(self.env_file)
        problems = env_problems(values)
        if problems:
            raise Refused(f"{self.env_file} is not drill-ready: " + "; ".join(problems))

        if self.base_url is None:
            origin = values.get("EZCHAIN_PUBLIC_URL") or "http://127.0.0.1:8080"
            self.base_url = origin.rstrip("/")

        config = self._compose("config", "--quiet", timeout=60)
        if config.returncode != 0:
            raise Refused(f"docker compose config rejected the stack: {_tail(config.stderr)}")
        return f"docker {self.docker_version}, {self.compose_version}, config ok"

    def _up(self) -> str:
        self._attempted_stack = True
        started = time.monotonic()
        result = self._compose("up", "--build", "-d", timeout=self.build_timeout)
        if result.returncode != 0:
            raise DrillFailure(f"compose up failed: {_tail(result.stderr)}")
        return f"stack started in {time.monotonic() - started:.1f}s ({self.project})"

    def _ready(self) -> str:
        return self._poll_ready()

    def _migrate(self) -> str:
        result = self._compose("ps", "--all", "--format", "json", timeout=60)
        if result.returncode != 0:
            raise DrillFailure(f"compose ps failed: {_tail(result.stderr)}")
        rows = parse_ps(result.stdout)
        row = next((item for item in rows if item.get("Service") == "migrate"), None)
        if row is None:
            raise DrillFailure("compose ps reports no migrate service")
        state = str(row.get("State", "")).lower()
        exit_code = row.get("ExitCode")
        if state not in ("exited", "stopped") or exit_code != 0:
            raise DrillFailure(f"migrate did not exit 0 (state={state!r}, exit={exit_code!r})")
        return f"migrate exited 0; schema at {self.head}"

    def _paper(self) -> str:
        key = f"drill-{self._started[:19].replace(':', '').replace('-', '')}-{uuid4().hex[:8]}"
        payload = {
            "chain": self.chain,
            "token_address": self.token,
            "side": "buy",
            "amount_usd": "25",
            "idempotency_key": key,
        }
        status, order = self._http("POST", "/api/v1/paper/orders", payload)
        if status != 201 or not isinstance(order, Mapping):
            raise DrillFailure(f"paper buy answered HTTP {status}: {json.dumps(order)[:300]}")
        if order.get("status") != "filled":
            raise DrillFailure(
                f"paper buy {order.get('id')} was {order.get('status')!r} "
                f"({order.get('reason')!r}), not filled"
            )

        replay_status, replay = self._http("POST", "/api/v1/paper/orders", payload)
        if replay_status not in (200, 201) or not isinstance(replay, Mapping):
            raise DrillFailure(f"replayed order answered HTTP {replay_status}")
        if replay.get("id") != order.get("id"):
            raise DrillFailure(
                f"replay returned {replay.get('id')}, not the original {order.get('id')}"
            )

        status, report = self._http("GET", "/api/v1/paper/reconciliation")
        if status != 200 or not isinstance(report, Mapping) or report.get("ok") is not True:
            raise DrillFailure(
                f"reconciliation is not ok: HTTP {status} {json.dumps(report)[:300]}"
            )

        self.paper = {
            "order_id": order.get("id"),
            "quote_kind": order.get("quote_kind"),
            "idempotency_key": key,
            "reconciliations": len(report.get("checks") or []),
            "cash_usd": (report.get("totals") or {}).get("cash_usd"),
        }
        return (
            f"paper buy {order.get('id')} filled ({order.get('quote_kind')}); "
            f"replay identical; reconciliation ok"
        )

    def _account(self) -> dict[str, Any]:
        status, body = self._http("GET", "/api/v1/paper/account")
        if status != 200 or not isinstance(body, Mapping):
            raise DrillFailure(f"paper account answered HTTP {status}")
        return body

    def _restart(self) -> str:
        before = self._account()
        result = self._compose("restart", "api", timeout=180)
        if result.returncode != 0:
            raise DrillFailure(f"restart api failed: {_tail(result.stderr)}")
        self._poll_ready()
        after = self._account()
        if before.get("cash_usd") != after.get("cash_usd"):
            raise DrillFailure(
                f"cash changed across restart: {before.get('cash_usd')} -> {after.get('cash_usd')}"
            )
        before_positions = len(before.get("positions") or [])
        after_positions = len(after.get("positions") or [])
        if before_positions != after_positions:
            raise DrillFailure(
                f"positions changed across restart: {before_positions} -> {after_positions}"
            )
        status, report = self._http("GET", "/api/v1/paper/reconciliation")
        if status != 200 or not isinstance(report, Mapping) or report.get("ok") is not True:
            raise DrillFailure("reconciliation is not ok after the restart")
        return (
            f"api restarted; cash {after.get('cash_usd')} and {after_positions} position(s) "
            "unchanged; reconciliation ok"
        )

    def _exec(self, *args: str, stdin: bytes | None = None, timeout: float = 600.0) -> Any:
        result = self._compose("exec", "-T", "postgres", *args, stdin=stdin, timeout=timeout)
        if result.returncode != 0:
            raise DrillFailure(f"postgres exec failed ({args[-1][:60]}): {_tail(result.stderr)}")
        return result

    def _backup(self) -> str:
        dump = self._exec("sh", "-c", 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc')
        content = dump.stdout
        if not content:
            raise DrillFailure("pg_dump produced no output")
        if self.dest is not None:
            self.dest.mkdir(parents=True, exist_ok=True)
            directory = self.dest
        else:
            directory = Path(tempfile.mkdtemp(prefix="ezchain-drill-"))
        path = directory / "drill.dump"
        path.write_bytes(content)
        digest = sha256(content).hexdigest()
        self.artifacts = {
            "work_dir": str(directory),
            "dump": str(path),
            "dump_sha256": digest,
            "dump_bytes": len(content),
        }

        self._exec("sh", "-c", f'createdb -U "$POSTGRES_USER" "{SCRATCH_DATABASE}"')
        try:
            self._exec(
                "sh",
                "-c",
                f'pg_restore -U "$POSTGRES_USER" -d "{SCRATCH_DATABASE}" --no-owner',
                stdin=content,
            )
            source = self._count("ledger_entries", database='"$POSTGRES_DB"')
            restored = self._count("ledger_entries", database=f'"{SCRATCH_DATABASE}"')
        finally:
            self._exec("sh", "-c", f'dropdb -U "$POSTGRES_USER" "{SCRATCH_DATABASE}"')
        if source != restored:
            raise DrillFailure(f"restore lost rows: {source} ledger entries, {restored} restored")
        self.artifacts["ledger_entries"] = source
        return (
            f"dump {len(content)} bytes sha256 {digest[:12]}…; restored {restored} ledger "
            f"entries into {SCRATCH_DATABASE}"
        )

    def _count(self, table: str, *, database: str) -> int:
        result = self._exec(
            "sh",
            "-c",
            f'psql -U "$POSTGRES_USER" -d {database} -Atc "select count(*) from {table}"',
        )
        text = result.stdout.decode("utf-8", "replace").strip()
        try:
            return int(text)
        except ValueError:
            raise DrillFailure(f"counting {table} returned {text[:80]!r}") from None

    def _teardown(self) -> str:
        result = self._compose("down", "-v", "--remove-orphans", timeout=300)
        if result.returncode != 0:
            raise DrillFailure(f"compose down failed: {_tail(result.stderr)}")
        return f"{self.project} removed (volumes included)"

    # ----------------------------------------------------------------------- run

    def run(self) -> DrillResult:
        try:
            self._phase("static", self._static)
            if self.static_only:
                return self._result("passed")
            self._phase("preflight", self._preflight)
            self._phase("up", self._up)
            self._phase("ready", self._ready)
            self._phase("migrate", self._migrate)
            self._phase("paper", self._paper)
            self._phase("restart", self._restart)
            self._phase("backup", self._backup)
        except Refused:
            pass
        except DrillFailure:
            pass
        finally:
            if not self.static_only and self._attempted_stack:
                if self.keep:
                    self.phases.append(
                        Phase(
                            "teardown",
                            "skipped",
                            0.0,
                            f"kept by --keep: remove with `docker compose -p {self.project} -f "
                            f"{self.compose} down -v`",
                        )
                    )
                else:
                    try:
                        self._phase("teardown", self._teardown)
                    except (Refused, DrillFailure):
                        pass

        if self.refusal is not None:
            status = "skipped"
        elif self.failure is not None:
            status = "failed"
        else:
            status = "passed"
        return self._result(status)

    def _result(self, status: str) -> DrillResult:
        document: dict[str, Any] = {
            "tool": TOOL,
            "format_version": FORMAT_VERSION,
            "status": status,
            "ok": status == "passed",
            "mode": "static-only" if self.static_only else "compose",
            "started_at": self._started,
            "finished_at": _now(),
            "root": str(self.root),
            "compose": str(self.compose),
            "env_file": str(self.env_file),
            "project": self.project,
            "base_url": self.base_url,
            "head_revision": self.head,
            "docker": {
                "binary": self.docker_bin,
                "server_version": self.docker_version,
                "compose_version": self.compose_version,
            },
            "phases": [phase.document() for phase in self.phases],
            "paper": self.paper,
            "artifacts": self.artifacts,
            "commands": self.commands,
            "refusal": self.refusal,
            "failure": self.failure,
        }
        return DrillResult(status=status, phases=list(self.phases), document=document)


def drill(
    *,
    root: Path,
    compose: Path,
    env_file: Path,
    project: str = DEFAULT_PROJECT,
    base_url: str | None = None,
    docker: str | None = None,
    timeout: float = 180.0,
    build_timeout: float = 1800.0,
    dest: Path | None = None,
    keep: bool = False,
    static_only: bool = False,
    chain: str = DEFAULT_CHAIN,
    token: str = DEFAULT_TOKEN,
) -> DrillResult:
    return Drill(
        root=root,
        compose=compose,
        env_file=env_file,
        project=project,
        base_url=base_url,
        docker=docker,
        timeout=timeout,
        build_timeout=build_timeout,
        dest=dest,
        keep=keep,
        static_only=static_only,
        chain=chain,
        token=token,
    ).run()


__all__ = [
    "EXIT_FAILED",
    "EXIT_OK",
    "EXIT_REFUSED",
    "Drill",
    "DrillFailure",
    "DrillResult",
    "Phase",
    "Refused",
    "checkout_script_location",
    "compose_problems",
    "drill",
    "env_problems",
    "migration_chain",
    "parse_ps",
    "read_env_file",
]
