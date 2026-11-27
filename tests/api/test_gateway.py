"""The supervised gateway: one config, one data dir, safe binds and fail-closed probes.

The supervisor is injected with a launcher and a terminator so the lifecycle is tested
without spawning real children. Every credential below is an obviously invalid constant.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import ezchain_api.gateway as gateway
from ezchain_api.gateway import cli as gateway_cli
from ezchain_api.gateway import config as gateway_config
from ezchain_api.gateway import model as gateway_model
from ezchain_api.gateway import supervisor as gateway_supervisor
from ezchain_api.gateway.state import write_states
from ezchain_api.routers import system

TEST_TOKEN = "TEST_TOKEN_DO_NOT_USE_2c7b"
TEST_CERT_NAME = "listener.crt"
TEST_KEY_NAME = "listener.key"
DEAD_PID = 999_999_999
HEAD = "0031_pair_address_width"
BEHIND = "0002_genesis_index"

FakeLauncher = Callable[[Sequence[str], Path, dict[str, str]], int]


def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        gateway_config.TOKEN_ENV,
        gateway_config.TLS_CERT_ENV,
        gateway_config.TLS_KEY_ENV,
        gateway_config.CONFIG_ENV,
        gateway_config.DATA_DIR_ENV,
    ):
        monkeypatch.delenv(name, raising=False)


def config_at(tmp_path: Path, **overrides: object) -> gateway_config.GatewayConfig:
    return gateway_config.GatewayConfig(data_dir=tmp_path, **overrides)  # type: ignore[arg-type]


def fake_launcher(launched: list[Sequence[str]]) -> FakeLauncher:
    def launch(command: Sequence[str], log_path: Path, environment: dict[str, str]) -> int:
        launched.append(command)
        return 700 + len(launched)

    return launch


def resident_states() -> tuple[gateway_model.ComponentState, ...]:
    return tuple(
        gateway_model.ComponentState(
            name=name,
            status="in_api" if name in gateway_model.API_RESIDENT_COMPONENTS else "running",
        )
        for name in gateway_model.COMPONENTS
    )


def test_loopback_default_is_the_safe_starting_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)

    config = gateway_config.load_config(data_dir=tmp_path)

    assert config.bind == gateway_config.DEFAULT_BIND
    assert gateway_config.is_loopback_bind(config.bind)
    assert config.tls is False
    assert config.token is None
    assert gateway_config.bind_security_problems(config) == ()


@pytest.mark.parametrize(
    ("tls", "with_token", "with_certs", "expected"),
    [
        (False, False, False, "TLS"),
        (True, False, True, gateway_config.TOKEN_ENV),
        (False, True, False, "TLS"),
    ],
)
def test_non_loopback_without_tls_and_token_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    tls: bool,
    with_token: bool,
    with_certs: bool,
    expected: str,
) -> None:
    clean_environment(monkeypatch)
    if with_token:
        monkeypatch.setenv(gateway_config.TOKEN_ENV, TEST_TOKEN)
    cert = tmp_path / TEST_CERT_NAME if with_certs else None
    key = tmp_path / TEST_KEY_NAME if with_certs else None

    with pytest.raises(gateway_config.GatewayConfigError) as raised:
        gateway_config.load_config(
            data_dir=tmp_path, bind="0.0.0.0", tls=tls, tls_cert=cert, tls_key=key
        )

    assert expected in str(raised.value)


def test_tls_without_certificate_and_key_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    monkeypatch.setenv(gateway_config.TOKEN_ENV, TEST_TOKEN)

    with pytest.raises(gateway_config.GatewayConfigError, match="certificate"):
        gateway_config.load_config(data_dir=tmp_path, bind="0.0.0.0", tls=True)


def test_non_loopback_with_tls_token_and_certs_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    monkeypatch.setenv(gateway_config.TOKEN_ENV, TEST_TOKEN)
    cert, key = tmp_path / TEST_CERT_NAME, tmp_path / TEST_KEY_NAME

    config = gateway_config.load_config(
        data_dir=tmp_path, bind="0.0.0.0", tls=True, tls_cert=cert, tls_key=key
    )

    assert config.tls is True
    assert config.tls_cert == cert
    assert config.tls_key == key
    assert config.token == TEST_TOKEN
    assert gateway_config.bind_security_problems(config) == ()


def test_read_only_commands_can_load_an_unstartable_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    document = {"bind": "0.0.0.0", "tls": True, "tls_cert": "c", "tls_key": "k"}
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    config = gateway_config.load_config(path, validate_security=False)

    assert config.bind == "0.0.0.0"
    assert config.token is None


def test_token_is_never_written_to_the_snapshot(tmp_path: Path) -> None:
    config = config_at(tmp_path, token=TEST_TOKEN)

    config.save()
    snapshot = config.config_path.read_text(encoding="utf-8")

    assert TEST_TOKEN not in snapshot
    assert "token" not in json.loads(snapshot)
    assert TEST_TOKEN not in repr(config)


def test_tls_files_and_token_reach_the_api_child(tmp_path: Path) -> None:
    cert, key = tmp_path / TEST_CERT_NAME, tmp_path / TEST_KEY_NAME
    config = config_at(
        tmp_path,
        bind="0.0.0.0",
        tls=True,
        tls_cert=cert,
        tls_key=key,
        token=TEST_TOKEN,
    )

    argv = gateway_supervisor.commands_for(config)["api"]
    environment = gateway_supervisor.child_environment(config)

    assert "--ssl-certfile" in argv and str(cert) in argv
    assert "--ssl-keyfile" in argv and str(key) in argv
    assert environment[gateway_config.TOKEN_ENV] == TEST_TOKEN


def test_plaintext_loopback_api_command_has_no_tls_args(tmp_path: Path) -> None:
    argv = gateway_supervisor.commands_for(config_at(tmp_path))["api"]

    assert "--ssl-certfile" not in argv
    assert "--ssl-keyfile" not in argv


def test_start_stop_and_status_cover_every_component(tmp_path: Path) -> None:
    launched: list[Sequence[str]] = []
    terminated: list[int] = []
    supervisor = gateway_supervisor.Supervisor(
        config_at(tmp_path),
        launcher=fake_launcher(launched),
        terminator=terminated.append,
    )

    started = supervisor.start()
    stopped = supervisor.stop()
    status = gateway_model.build_status(
        config_at(tmp_path), applied_revision=HEAD, head_revision=HEAD
    )

    assert {state.name for state in started} == set(gateway_model.COMPONENTS)
    assert all(state.readable for state in started)
    assert len(launched) == len(gateway_model.PROCESS_COMPONENTS)
    api = next(state for state in started if state.name == "api")
    assert api.status == "running" and api.pid is not None
    engine = next(state for state in started if state.name == "engine")
    assert engine.status == "in_api"
    assert all(state.status == "stopped" for state in stopped)
    assert set(terminated) == {state.pid for state in started if state.pid is not None}
    assert status.ok and status.ready
    assert {component.name for component in status.components} == set(gateway_model.COMPONENTS)


def test_supervisor_refuses_an_unsafe_bind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clean_environment(monkeypatch)
    config = config_at(
        tmp_path, bind="0.0.0.0", tls=True, tls_cert=tmp_path / "c", tls_key=tmp_path / "k"
    )
    supervisor = gateway_supervisor.Supervisor(
        config, launcher=fake_launcher([]), terminator=lambda pid: None
    )

    with pytest.raises(gateway_config.GatewayConfigError):
        supervisor.start()
    assert not config.state_path.exists()


def test_launch_failure_exits_nonzero_without_a_success_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_launch(command: Sequence[str], log_path: Path, environment: dict[str, str]) -> int:
        if "uvicorn" in command:
            raise OSError("listener could not start")
        return 42

    config = config_at(tmp_path)
    supervisor = gateway_supervisor.Supervisor(
        config, launcher=failing_launch, terminator=lambda pid: None
    )
    monkeypatch.setattr(gateway_cli, "Supervisor", lambda supplied, **kwargs: supervisor)

    code = gateway_cli.run_start(config)
    output = capsys.readouterr().out

    assert code != 0
    assert "gateway: started" not in output
    assert "api" in output


def test_unreadable_state_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gateway_cli, "_migration_facts", lambda: (HEAD, HEAD))

    code = gateway_cli.run_status(config_at(tmp_path))
    output = capsys.readouterr().out

    assert code == 1
    assert "unreadable" in output
    assert "result: NOT READY" in output


def test_readyz_fails_while_the_schema_is_behind(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = config_at(tmp_path)
    write_states(config, resident_states())

    code = gateway_cli.run_readyz(
        config, applied_revision=lambda: BEHIND, head_revision=lambda: HEAD
    )
    body = json.loads(capsys.readouterr().out)

    assert code == 1
    assert body["status"] == "not_ready"
    assert body["migration"] == "pending"


def test_readyz_reflects_a_dead_api_process(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = config_at(tmp_path)
    states = tuple(
        gateway_model.ComponentState(name=state.name, status=state.status, pid=DEAD_PID)
        if state.name == "api"
        else state
        for state in resident_states()
    )
    write_states(config, states)

    code = gateway_cli.run_readyz(config, applied_revision=lambda: HEAD, head_revision=lambda: HEAD)
    body = json.loads(capsys.readouterr().out)

    assert code == 1
    assert body["status"] == "not_ready"
    assert any(
        "process is not running" in line
        for line in gateway_model.build_status(
            config, states, applied_revision=HEAD, head_revision=HEAD, check_live=True
        ).problems
    )


def test_healthz_cli_returns_the_liveness_body(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = gateway_cli.run_healthz()

    assert code == 0
    assert json.loads(capsys.readouterr().out) == {"status": "ok"}


def test_main_refuses_an_unsafe_start(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clean_environment(monkeypatch)

    code = gateway_cli.main(["start", "--data-dir", str(tmp_path), "--bind", "0.0.0.0"])

    assert code == 2
    assert "refused to start" in capsys.readouterr().out


def test_http_probes_are_served_by_the_api(client_for: Callable[..., TestClient]) -> None:
    client = client_for(system.router)

    liveness = client.get("/healthz")
    readiness = client.get("/readyz")

    assert liveness.status_code == 200
    assert liveness.json()["status"] == "ok"
    assert readiness.status_code in {200, 503}
    assert "migration" in readiness.json()


def test_entry_points_import_no_signing_or_key_material() -> None:
    package_dir = Path(gateway.__file__).parent
    forbidden = (
        "ezchain_executor",
        "ezchain_agent_runtime",
        "signing",
        "vault",
        "keypair",
        "private_key",
        "seed_phrase",
        "solders",
        "solana",
    )
    for filename in ("cli.py", "__main__.py"):
        source = (package_dir / filename).read_text(encoding="utf-8").lower()
        for term in forbidden:
            assert term not in source, f"{term!r} found in {filename}"


def test_gateway_delegates_from_the_api_cli() -> None:
    source = (Path(gateway.__file__).parent.parent / "cli.py").read_text(encoding="utf-8")

    assert "gateway.cli" in source and "gateway_main" in source
