"""The Compose drill without a Docker daemon: parsing, refusals and every phase path.

The drill's contract is its exit code and its phase list, so both are asserted here. A
fake ``docker`` on ``PATH`` and a stub edge server stand in for the daemon and the stack;
nothing in this file claims a container ever started. The cases that matter are the ones
that must not read as a pass: a missing daemon, a daemon that answers but cannot start the
stack, a schema behind head, a paper order that did not fill, a restart that loses rows,
and a restore that comes back short.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml
from tools.ops import drill as drill_module
from tools.ops.cli import main
from tools.ops.drill import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_REFUSED,
    DrillFailure,
    DrillResult,
    Phase,
    checkout_script_location,
    compose_problems,
    drill,
    env_problems,
    migration_chain,
    parse_ps,
    read_env_file,
)

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "infra" / "compose.yaml"
TOKEN = "a" * 32
PASSWORD = "b" * 32

FAKE_DOCKER = '''#!{python}
"""A stand-in for the docker CLI: records its arguments and answers from files."""
import json
import os
import sys

state = os.path.join(os.path.dirname(os.path.realpath(__file__)), "state")
args = sys.argv[1:]

with open(os.path.join(state, "calls.jsonl"), "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\\n")


def setting(name, default):
    try:
        with open(os.path.join(state, name), encoding="utf-8") as handle:
            return handle.read().strip()
    except FileNotFoundError:
        return default


def reply(text, code=0):
    sys.stdout.write(text)
    sys.exit(int(code))


def fail(text):
    sys.stderr.write(text)
    sys.exit(1)


if not args:
    fail("no arguments")

if args[0] == "info":
    if setting("info_rc", "0") != "0":
        fail("Cannot connect to the Docker daemon at unix:///var/run/docker.sock")
    reply("27.0.0\\n")

if args[0] == "compose" and args[1] == "version":
    reply("Docker Compose version v2.30.0\\n")

if args[0] == "compose" and "config" in args:
    if setting("config_rc", "0") != "0":
        fail("services.api.environment must be a mapping")
    reply("")

if args[0] == "compose" and "up" in args:
    if setting("up_rc", "0") != "0":
        fail("failed to build: no space left on device")
    reply("started\\n")

if args[0] == "compose" and "ps" in args:
    migrate_exit = setting("migrate_exit", "0")
    state_name = "exited" if setting("migrate_state", "exited") == "exited" else "running"
    rows = [
        {"Service": "postgres", "State": "running", "ExitCode": 0},
        {"Service": "migrate", "State": state_name, "ExitCode": int(migrate_exit)},
        {"Service": "api", "State": "running", "ExitCode": 0},
    ]
    if setting("ps_format", "json") == "lines":
        reply("\\n".join(json.dumps(row) for row in rows) + "\\n")
    reply(json.dumps(rows) + "\\n")

if args[0] == "compose" and "restart" in args:
    with open(os.path.join(state, "restarts"), "a", encoding="utf-8") as log:
        log.write(" ".join(args) + "\\n")
    reply("restarted\\n")

if args[0] == "compose" and "down" in args:
    with open(os.path.join(state, "down"), "a", encoding="utf-8") as log:
        log.write(" ".join(args) + "\\n")
    if setting("down_rc", "0") != "0":
        fail("network still in use")
    reply("removed\\n")

if args[0] == "compose" and "exec" in args:
    script = args[-1]
    if "pg_dump" in script:
        sys.stdout.buffer.write(b"PGDMP-fake-dump")
        sys.exit(0)
    if "pg_restore" in script:
        payload = sys.stdin.buffer.read()
        if not payload:
            fail("no dump on stdin")
        sys.exit(int(setting("restore_rc", "0")))
    if "createdb" in script or "dropdb" in script:
        sys.exit(0)
    if "select count" in script:
        scratch = "scratch" in script
        reply(setting("psql_scratch" if scratch else "psql_source", "3") + "\\n")

fail("unexpected docker invocation: " + " ".join(args))
'''


class FakeDocker:
    """A fake ``docker`` executable plus the state file that drives its answers."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.state = directory / "state"
        self.state.mkdir(parents=True, exist_ok=True)
        self.binary = directory / "docker"
        self.binary.write_text(
            FAKE_DOCKER.replace("#!{python}", f"#!{sys.executable}", 1), encoding="utf-8"
        )
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)

    def set(self, name: str, value: object) -> None:
        (self.state / name).write_text(str(value), encoding="utf-8")

    def calls(self) -> list[list[str]]:
        log = self.state / "calls.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]

    def subcommands(self) -> list[str]:
        return [" ".join(call[1:3]) if call[0] == "compose" else call[0] for call in self.calls()]

    def marker(self, name: str) -> bool:
        return (self.state / name).exists()


class Edge:
    """A stub edge: ``/readyz``, the paper order path, the account and reconciliation."""

    def __init__(self) -> None:
        # The fake mirrors the real chain so a new migration does not make this
        # test lie about the head it is drilling to.
        self.head, _, _ = migration_chain(checkout_script_location(ROOT))
        self.revision = self.head
        self.orders: dict[str, dict[str, Any]] = {}
        self.order_status = "filled"
        self.reconciliation_ok = True
        self.cash = "9974.92500000"
        self.positions = 1
        self.ready_after = 0
        self.ready_checks = 0
        self.requests: list[tuple[str, str]] = []

    def dispatch(self, method: str, path: str, body: bytes) -> tuple[int, Any]:
        self.requests.append((method, path))
        if path == "/readyz":
            self.ready_checks += 1
            if self.ready_checks <= self.ready_after:
                return 503, {"status": "not_ready", "migration": "pending", "db": "ok"}
            return 200, {
                "status": "ready",
                "db": "ok",
                "migration": "head",
                "revision": self.revision,
                "head": self.head,
            }
        if path == "/api/v1/paper/orders" and method == "POST":
            payload = json.loads(body)
            key = payload["idempotency_key"]
            if key not in self.orders:
                self.orders[key] = {
                    "id": f"order-{len(self.orders) + 1}",
                    "status": self.order_status,
                    "quote_kind": "paper_mark",
                    "reason": None if self.order_status == "filled" else "INSUFFICIENT_CASH",
                }
            return 201, self.orders[key]
        if path == "/api/v1/paper/account":
            return 200, {"cash_usd": self.cash, "positions": [{}] * self.positions}
        if path == "/api/v1/paper/reconciliation":
            return 200, {
                "ok": self.reconciliation_ok,
                "checks": [{"id": "cash", "ok": True, "detail": "ok"}],
                "totals": {"cash_usd": self.cash},
            }
        return 404, {"detail": "not found"}


@pytest.fixture
def edge() -> Iterator[Edge]:
    state = Edge()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: object) -> None:
            return

        def _respond(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length) if length else b""
            code, payload = state.dispatch(self.command, self.path.split("?")[0], body)
            data = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - http.server's spelling
            self._respond()

        def do_POST(self) -> None:  # noqa: N802 - http.server's spelling
            self._respond()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.base_url = f"http://127.0.0.1:{server.server_address[1]}"
    yield state
    server.shutdown()
    server.server_close()


@pytest.fixture
def fake_docker(tmp_path: Path) -> FakeDocker:
    return FakeDocker(tmp_path / "bin")


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    path = tmp_path / "infra.env"
    path.write_text(
        f"POSTGRES_PASSWORD={PASSWORD}\n"
        f"EZCHAIN_API_TOKEN={TOKEN}\n"
        "EZCHAIN_PUBLIC_URL=http://127.0.0.1:8080\n",
        encoding="utf-8",
    )
    return path


def _document() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _run(fake_docker: FakeDocker, env_file: Path, base_url: str, **kwargs: Any) -> DrillResult:
    options: dict[str, Any] = {
        "root": ROOT,
        "compose": COMPOSE,
        "env_file": env_file,
        "docker": str(fake_docker.binary),
        "base_url": base_url,
        "timeout": 5.0,
    }
    options.update(kwargs)
    return drill(**options)


# --------------------------------------------------------------------- parsing


def test_the_real_compose_file_has_the_structure_a_drill_needs() -> None:
    assert compose_problems(_document()) == []


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda doc: doc["services"].pop("caddy"), "missing service(s): caddy"),
        (
            lambda doc: doc["services"]["api"].pop("healthcheck"),
            "api has no healthcheck test",
        ),
        (
            lambda doc: doc["services"]["api"].__setitem__("ports", ["8000:8000"]),
            "api publishes a host port; only caddy may",
        ),
        (
            lambda doc: doc["services"]["migrate"].__setitem__("restart", "always"),
            "migrate restarts",
        ),
        (
            lambda doc: doc["services"]["migrate"].__setitem__("command", ["alembic", "check"]),
            "migrate does not run 'alembic upgrade head'",
        ),
        (
            lambda doc: doc["services"]["api"]["depends_on"].__setitem__(
                "migrate", {"condition": "service_started"}
            ),
            "api does not wait for migrate to complete successfully",
        ),
        (
            lambda doc: doc["networks"]["data"].__setitem__("internal", False),
            "network 'data' is not internal",
        ),
        (
            lambda doc: doc["services"]["postgres"].__setitem__("networks", ["data", "app"]),
            "postgres is attached to",
        ),
    ],
)
def test_the_structural_checks_name_what_they_refuse(mutation: Any, expected: str) -> None:
    document = _document()
    mutation(document)

    problems = compose_problems(document)

    assert any(expected in problem for problem in problems), problems


def test_a_compose_file_without_services_is_refused() -> None:
    assert compose_problems({}) == ["compose document has no services mapping"]


def test_the_env_file_is_read_as_plain_key_values(tmp_path: Path) -> None:
    path = tmp_path / "infra.env"
    path.write_text(
        "# a comment\n\nPOSTGRES_DB=ezchain\nEZCHAIN_API_TOKEN='quoted'\nnot-a-pair\n",
        encoding="utf-8",
    )

    assert read_env_file(path) == {"POSTGRES_DB": "ezchain", "EZCHAIN_API_TOKEN": "quoted"}


def test_a_placeholder_env_file_is_refused_by_name_and_never_by_value(
    env_file: Path,
) -> None:
    values = read_env_file(env_file)
    values["POSTGRES_PASSWORD"] = "replace-with-a-long-random-password"

    problems = env_problems(values)

    assert problems == ["POSTGRES_PASSWORD is a placeholder or shorter than 16 characters"]
    assert all(PASSWORD not in problem and TOKEN not in problem for problem in problems)


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("EZCHAIN_API_TOKEN", "short", "EZCHAIN_API_TOKEN must be at least 32 characters"),
        ("EZCHAIN_PUBLIC_URL", "localhost:8080", "EZCHAIN_PUBLIC_URL must be an http"),
    ],
)
def test_env_problems_are_typed(key: str, value: str, expected: str) -> None:
    values = {
        "POSTGRES_PASSWORD": PASSWORD,
        "EZCHAIN_API_TOKEN": TOKEN,
        "EZCHAIN_PUBLIC_URL": "http://localhost:8080",
    }
    values[key] = value

    assert expected in " ".join(env_problems(values))


def test_a_missing_env_key_is_named() -> None:
    problems = env_problems({"POSTGRES_PASSWORD": PASSWORD})

    assert "EZCHAIN_API_TOKEN is missing" in problems
    assert "EZCHAIN_PUBLIC_URL is missing" in problems


def test_the_repository_chain_has_exactly_one_head() -> None:
    head, count, problems = migration_chain(checkout_script_location(ROOT))

    assert problems == []
    # The head moves with the schema; the invariant is one head and a count that
    # matches the revisions on disk, never a frozen revision string.
    revisions = len(list((ROOT / "apps" / "api" / "alembic" / "versions").glob("0*.py")))
    assert head.startswith("0")
    assert count == revisions


def test_the_drill_reads_the_chain_of_its_own_checkout(tmp_path: Path) -> None:
    """`head` must be this tree's head, not whichever tree the install points at."""
    assert checkout_script_location(tmp_path) is None
    assert checkout_script_location(ROOT) == ROOT / "apps" / "api" / "alembic"


def test_two_heads_are_reported_as_a_missing_merge(tmp_path: Path) -> None:
    versions = tmp_path / "versions"
    versions.mkdir()
    (versions / "a.py").write_text('revision = "a"\ndown_revision = None\n', encoding="utf-8")
    (versions / "b.py").write_text('revision = "b"\ndown_revision = None\n', encoding="utf-8")

    _, count, problems = migration_chain(tmp_path)

    assert count == 2
    assert problems == ["2 heads (a, b); a merge is missing"]


def test_a_branch_point_is_reported(tmp_path: Path) -> None:
    versions = tmp_path / "versions"
    versions.mkdir()
    (versions / "a.py").write_text('revision = "a"\ndown_revision = None\n', encoding="utf-8")
    (versions / "b.py").write_text('revision = "b"\ndown_revision = "a"\n', encoding="utf-8")
    (versions / "c.py").write_text('revision = "c"\ndown_revision = "a"\n', encoding="utf-8")
    (versions / "d.py").write_text('revision = "d"\ndown_revision = ("b", "c")\n', encoding="utf-8")

    _, _, problems = migration_chain(tmp_path)

    assert "a is a branch point (2 children)" in problems


def test_an_empty_migration_directory_is_not_a_head(tmp_path: Path) -> None:
    (tmp_path / "versions").mkdir()

    head, count, problems = migration_chain(tmp_path)

    assert head == ""
    assert count == 0
    assert problems == ["the migration directory has no head revision"]


def test_compose_ps_is_parsed_as_an_array_and_as_lines() -> None:
    rows = [{"Service": "api", "State": "running"}]

    assert parse_ps(json.dumps(rows).encode()) == rows
    assert parse_ps(b"\n".join(json.dumps(row).encode() for row in rows)) == rows
    assert parse_ps(b"") == []


def test_compose_ps_refuses_an_unexpected_document() -> None:
    with pytest.raises(DrillFailure, match="unexpected document"):
        parse_ps(b'"a string"')


def test_exit_codes_follow_the_status() -> None:
    def result(status: str) -> DrillResult:
        return DrillResult(status=status)

    assert result("passed").exit_code == EXIT_OK
    assert result("failed").exit_code == EXIT_FAILED
    assert result("skipped").exit_code == EXIT_REFUSED


def test_a_phase_document_is_json_serialisable() -> None:
    document = Phase("up", "passed", 1.23456, "started").document()

    assert document == {"name": "up", "status": "passed", "seconds": 1.235, "detail": "started"}


# ------------------------------------------------------------------ fake daemon


def test_the_drill_runs_every_phase_against_a_fake_daemon(
    fake_docker: FakeDocker, env_file: Path, edge: Edge
) -> None:
    result = _run(fake_docker, env_file, edge.base_url)

    assert result.status == "passed", result.document["failure"]
    assert [phase.name for phase in result.phases] == [
        "static",
        "preflight",
        "up",
        "ready",
        "migrate",
        "paper",
        "restart",
        "backup",
        "teardown",
    ]
    assert result.document["mode"] == "compose"
    assert result.document["head_revision"] == edge.head
    assert result.document["paper"]["quote_kind"] == "paper_mark"
    assert result.document["artifacts"]["ledger_entries"] == 3
    assert result.document["docker"]["server_version"] == "27.0.0"
    assert result.exit_code == EXIT_OK


def test_the_drill_starts_and_removes_its_own_project(
    fake_docker: FakeDocker, env_file: Path, edge: Edge
) -> None:
    _run(fake_docker, env_file, edge.base_url)

    joined = [" ".join(call) for call in fake_docker.calls()]
    up = next(command for command in joined if " up " in f" {command} ")
    down = next(command for command in joined if " down " in f" {command} ")
    assert "-p ezchain-verify" in up and "--build" in up
    assert "--remove-orphans" in down and "-v" in down
    assert fake_docker.marker("down")


def test_the_drill_records_the_commands_it_ran(
    fake_docker: FakeDocker, env_file: Path, edge: Edge
) -> None:
    result = _run(fake_docker, env_file, edge.base_url)

    commands = [" ".join(call) for call in result.document["commands"]]
    assert any("compose" in command and "config" in command for command in commands)
    assert any("exec" in command and "pg_dump" in command for command in commands)
    assert all(str(fake_docker.binary) in command or "exec" in command for command in commands)


def test_a_static_only_run_never_calls_docker(fake_docker: FakeDocker, tmp_path: Path) -> None:
    result = drill(
        root=ROOT,
        compose=COMPOSE,
        env_file=tmp_path / "absent.env",
        docker=str(fake_docker.binary),
        static_only=True,
    )

    assert result.status == "passed"
    assert [phase.name for phase in result.phases] == ["static"]
    assert fake_docker.calls() == []
    assert result.document["docker"]["server_version"] is None


def test_the_cli_prints_a_static_pass_and_exits_zero() -> None:
    assert main(["drill", "--static-only"]) == EXIT_OK


def test_the_cli_refuses_when_docker_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")

    assert main(["drill", "--docker", "docker-does-not-exist"]) == EXIT_REFUSED


def test_the_cli_chain_verb_answers_head() -> None:
    assert main(["chain"]) == EXIT_OK


# --------------------------------------------------------------------- refusals


def test_no_docker_cli_is_a_refusal_not_a_pass(tmp_path: Path, env_file: Path) -> None:
    result = drill(
        root=ROOT,
        compose=COMPOSE,
        env_file=env_file,
        docker=str(tmp_path / "nope"),
        timeout=1.0,
    )

    assert result.status == "skipped"
    assert result.exit_code == EXIT_REFUSED
    assert "not on PATH" in str(result.document["refusal"])
    # A refusal before `up` never started anything, so there is nothing to tear down and
    # no phase after preflight may claim a result.
    assert [(phase.name, phase.status) for phase in result.phases] == [
        ("static", "passed"),
        ("preflight", "skipped"),
    ]
    assert result.document["mode"] == "compose"


def test_an_unreachable_daemon_is_a_refusal(fake_docker: FakeDocker, env_file: Path) -> None:
    fake_docker.set("info_rc", "1")

    result = _run(fake_docker, env_file, "http://127.0.0.1:1")

    assert result.status == "skipped"
    assert result.exit_code == EXIT_REFUSED
    assert "no Docker daemon reachable" in str(result.document["refusal"])


def test_a_missing_env_file_is_a_refusal(fake_docker: FakeDocker, tmp_path: Path) -> None:
    result = drill(
        root=ROOT,
        compose=COMPOSE,
        env_file=tmp_path / "absent.env",
        docker=str(fake_docker.binary),
        base_url="http://127.0.0.1:1",
        timeout=1.0,
    )

    assert result.exit_code == EXIT_REFUSED
    assert "is missing" in str(result.document["refusal"])


def test_a_placeholder_env_file_is_a_refusal(fake_docker: FakeDocker, tmp_path: Path) -> None:
    path = tmp_path / "infra.env"
    path.write_text(
        "POSTGRES_PASSWORD=replace-with-a-long-random-password\n"
        f"EZCHAIN_API_TOKEN={TOKEN}\n"
        "EZCHAIN_PUBLIC_URL=http://127.0.0.1:8080\n",
        encoding="utf-8",
    )

    result = drill(
        root=ROOT,
        compose=COMPOSE,
        env_file=path,
        docker=str(fake_docker.binary),
        base_url="http://127.0.0.1:1",
        timeout=1.0,
    )

    assert result.exit_code == EXIT_REFUSED
    assert "not drill-ready" in str(result.document["refusal"])
    assert PASSWORD not in str(result.document)


def test_a_compose_config_rejection_is_a_refusal(fake_docker: FakeDocker, env_file: Path) -> None:
    fake_docker.set("config_rc", "1")

    result = _run(fake_docker, env_file, "http://127.0.0.1:1")

    assert result.exit_code == EXIT_REFUSED
    assert "compose config rejected" in str(result.document["refusal"])


def test_a_broken_compose_file_is_refused_before_docker(
    fake_docker: FakeDocker, env_file: Path, tmp_path: Path
) -> None:
    broken = tmp_path / "compose.yaml"
    broken.write_text("services: [oops\n", encoding="utf-8")

    result = drill(root=ROOT, compose=broken, env_file=env_file, docker=str(fake_docker.binary))

    assert result.exit_code == EXIT_REFUSED
    assert "not valid YAML" in str(result.document["refusal"])
    assert fake_docker.calls() == []


# --------------------------------------------------------------------- failures


def test_a_failed_build_fails_the_drill_and_still_tears_down(
    fake_docker: FakeDocker, env_file: Path
) -> None:
    fake_docker.set("up_rc", "1")

    result = _run(fake_docker, env_file, "http://127.0.0.1:1")

    assert result.status == "failed"
    assert result.exit_code == EXIT_FAILED
    assert "compose up failed" in str(result.document["failure"])
    assert fake_docker.marker("down")


def test_a_schema_behind_head_fails_readiness(
    fake_docker: FakeDocker, env_file: Path, edge: Edge
) -> None:
    edge.revision = "0021_approvals"

    result = _run(fake_docker, env_file, edge.base_url, timeout=1.0)

    assert result.status == "failed"
    assert "did not report head" in str(result.document["failure"])
    assert edge.head in str(result.document["failure"])
    assert "0021_approvals" in str(result.document["failure"])


def test_readiness_is_polled_until_the_schema_is_at_head(
    fake_docker: FakeDocker, env_file: Path, edge: Edge
) -> None:
    edge.ready_after = 2

    result = _run(fake_docker, env_file, edge.base_url, timeout=10.0)

    assert result.status == "passed", result.document["failure"]
    assert edge.ready_checks >= 3


def test_a_migration_job_that_did_not_exit_zero_fails_the_drill(
    fake_docker: FakeDocker, env_file: Path, edge: Edge
) -> None:
    fake_docker.set("migrate_exit", "1")

    result = _run(fake_docker, env_file, edge.base_url)

    assert result.status == "failed"
    assert "migrate did not exit 0" in str(result.document["failure"])


def test_a_migration_job_still_running_fails_the_drill(
    fake_docker: FakeDocker, env_file: Path, edge: Edge
) -> None:
    fake_docker.set("migrate_state", "running")

    result = _run(fake_docker, env_file, edge.base_url)

    assert "state='running'" in str(result.document["failure"])


def test_compose_ps_line_output_is_accepted(
    fake_docker: FakeDocker, env_file: Path, edge: Edge
) -> None:
    fake_docker.set("ps_format", "lines")

    result = _run(fake_docker, env_file, edge.base_url)

    assert result.status == "passed", result.document["failure"]


def test_an_order_that_did_not_fill_fails_the_paper_phase(
    fake_docker: FakeDocker, env_file: Path, edge: Edge
) -> None:
    edge.order_status = "rejected"

    result = _run(fake_docker, env_file, edge.base_url)

    assert result.status == "failed"
    assert "not filled" in str(result.document["failure"])
    assert fake_docker.marker("down")


def test_a_second_order_id_for_a_replay_fails(
    fake_docker: FakeDocker, env_file: Path, edge: Edge, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = edge.dispatch
    seen = {"posts": 0}

    def renumbered(method: str, path: str, body: bytes) -> tuple[int, Any]:
        code, payload = original(method, path, body)
        if path == "/api/v1/paper/orders" and method == "POST" and isinstance(payload, dict):
            seen["posts"] += 1
            payload = {**payload, "id": f"order-{seen['posts']}"}
        return code, payload

    monkeypatch.setattr(edge, "dispatch", renumbered)

    result = _run(fake_docker, env_file, edge.base_url)

    assert result.status == "failed"
    assert "not the original" in str(result.document["failure"])


def test_a_failed_reconciliation_fails_the_paper_phase(
    fake_docker: FakeDocker, env_file: Path, edge: Edge
) -> None:
    edge.reconciliation_ok = False

    result = _run(fake_docker, env_file, edge.base_url)

    assert result.status == "failed"
    assert "reconciliation is not ok" in str(result.document["failure"])


def test_cash_changing_across_a_restart_fails(
    fake_docker: FakeDocker, env_file: Path, edge: Edge, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = edge.dispatch
    calls = {"n": 0}

    def shifting(method: str, path: str, body: bytes) -> tuple[int, Any]:
        if path == "/api/v1/paper/account":
            calls["n"] += 1
            edge.cash = "10000.00000000" if calls["n"] > 1 else "9974.92500000"
        return original(method, path, body)

    monkeypatch.setattr(edge, "dispatch", shifting)

    result = _run(fake_docker, env_file, edge.base_url)

    assert result.status == "failed"
    assert "cash changed across restart" in str(result.document["failure"])
    assert fake_docker.marker("restarts")


def test_a_short_restore_fails_the_backup_phase(
    fake_docker: FakeDocker, env_file: Path, edge: Edge
) -> None:
    fake_docker.set("psql_source", "3")
    fake_docker.set("psql_scratch", "2")

    result = _run(fake_docker, env_file, edge.base_url)

    assert result.status == "failed"
    assert "restore lost rows: 3 ledger entries, 2 restored" in str(result.document["failure"])
    assert fake_docker.marker("down")


def test_a_restore_that_failed_fails_the_backup_phase(
    fake_docker: FakeDocker, env_file: Path, edge: Edge
) -> None:
    fake_docker.set("restore_rc", "1")

    result = _run(fake_docker, env_file, edge.base_url)

    assert "postgres exec failed" in str(result.document["failure"])


def test_keep_leaves_the_project_and_says_how_to_remove_it(
    fake_docker: FakeDocker, env_file: Path, edge: Edge
) -> None:
    result = _run(fake_docker, env_file, edge.base_url, keep=True)

    assert result.status == "passed"
    teardown = next(phase for phase in result.phases if phase.name == "teardown")
    assert teardown.status == "skipped"
    assert "down -v" in teardown.detail
    assert not fake_docker.marker("down")


def test_a_failed_teardown_fails_the_drill(
    fake_docker: FakeDocker, env_file: Path, edge: Edge
) -> None:
    """The drill promises removal, so failing to remove is a failure, not a footnote."""
    fake_docker.set("down_rc", "1")

    result = _run(fake_docker, env_file, edge.base_url)

    teardown = next(phase for phase in result.phases if phase.name == "teardown")
    assert teardown.status == "failed"
    assert result.status == "failed"
    assert "compose down failed" in str(result.document["failure"])
    assert next(phase for phase in result.phases if phase.name == "paper").status == "passed"


def test_the_dump_is_written_when_a_destination_is_given(
    fake_docker: FakeDocker, env_file: Path, edge: Edge, tmp_path: Path
) -> None:
    dest = tmp_path / "out"
    result = _run(fake_docker, env_file, edge.base_url, dest=dest)

    assert result.status == "passed", result.document["failure"]
    dump = dest / "drill.dump"
    assert dump.read_bytes() == b"PGDMP-fake-dump"
    assert result.document["artifacts"]["dump_bytes"] == len(b"PGDMP-fake-dump")
    assert len(str(result.document["artifacts"]["dump_sha256"])) == 64


def test_an_unexpected_exception_is_a_failure_not_a_pass(
    fake_docker: FakeDocker, env_file: Path, edge: Edge, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(self: drill_module.Drill) -> str:
        raise ValueError("boom")

    monkeypatch.setattr(drill_module.Drill, "_restart", explode)

    result = _run(fake_docker, env_file, edge.base_url)

    assert result.status == "failed"
    assert "ValueError: boom" in str(result.document["failure"])


def test_the_summary_never_prints_a_secret(
    fake_docker: FakeDocker, env_file: Path, edge: Edge
) -> None:
    result = _run(fake_docker, env_file, edge.base_url)

    rendered = json.dumps(result.document)
    assert PASSWORD not in rendered
    assert TOKEN not in rendered


def test_the_script_is_executable_and_points_at_the_module() -> None:
    script = ROOT / "tools" / "ops" / "drill.sh"

    assert os.access(script, os.X_OK)
    text = script.read_text(encoding="utf-8")
    assert "-m tools.ops drill" in text
    assert "EZCHAIN_GATE_DRILL" in text


def test_the_gate_never_runs_the_drill_by_default() -> None:
    gate = (ROOT / "tools" / "ci.sh").read_text(encoding="utf-8")

    assert "EZCHAIN_GATE_DRILL" in gate
    assert "${EZCHAIN_GATE_DRILL:-off}" in gate
    assert "--static-only" in gate
    assert "tools/ops/drill.sh" in gate


def test_the_gate_rejects_an_unknown_drill_mode() -> None:
    gate = (ROOT / "tools" / "ci.sh").read_text(encoding="utf-8")

    assert "must be off, static or run" in gate
    assert "exit 2" in gate
