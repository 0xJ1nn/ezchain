"""Contract tests for the native (non-Docker) installer.

The installer in ``tools/install-native.sh`` is the primary self-hosting path: a Python
3.11 venv, the PyO3 bridge, the Next.js standalone server and two systemd *user* units,
with no container runtime and no root.

These tests prove what a normal host can prove without installing anything:

* ``--dry-run`` exits 0, writes nothing outside a temporary HOME and reaches no network;
* the two unit files are syntactically valid for systemd (``systemd-analyze verify`` when
  the host has it, otherwise an assertion on the required directives);
* ``apps/api/Dockerfile`` carries a Rust builder stage and installs ``ezchain_bridge.so``
  into the runtime site-packages. This one is a **static** assertion: there is no Docker
  daemon in this test environment, so the image is not built here;
* ``--configure-only`` writes an environment file that is mode 0600 and whose generated
  token never appears in the installer's output;
* a real install refuses an install root the units' ``PrivateTmp`` would hide, and the
  environment file survives a rewrite with a value that itself contains ``=``.

No test starts a service, migrates a database or calls the network.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "tools" / "install-native.sh"
UNIT_DIR = ROOT / "tools" / "install" / "native"
API_UNIT = UNIT_DIR / "ezchain-api.service"
WEB_UNIT = UNIT_DIR / "ezchain-web.service"
DOCKERFILE = ROOT / "apps" / "api" / "Dockerfile"

DATABASE_URL = "postgresql+psycopg://ezchain:not-a-real-secret@127.0.0.1:5432/ezchain"
SECRET_IN_URL = "not-a-real-secret"

REQUIRED_SERVICE_DIRECTIVES = (
    "Restart=on-failure",
    "NoNewPrivileges=yes",
    "PrivateTmp=yes",
    "ProtectSystem=strict",
    "EnvironmentFile=",
    "ReadWritePaths=",
)

# Tools that would have to run for a network or build side effect. A dry run must not
# invoke any of them; each stub records the call and fails if it is used.
NETWORK_TOOLS = ("curl", "wget", "pip", "pip3", "pnpm", "npm", "cargo", "git")


def run_installer(
    *arguments: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(INSTALLER), *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env={**os.environ, **(env or {})},
        check=False,
    )


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def entries(path: Path) -> set[str]:
    return {str(item.relative_to(path)) for item in path.rglob("*")}


def write_network_stubs(bin_dir: Path, marker: Path) -> None:
    """Put failing stubs for every build/network tool first on PATH."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    for tool in NETWORK_TOOLS:
        stub = bin_dir / tool
        stub.write_text(
            f"#!/bin/sh\nprintf '%s\\n' \"{tool} $*\" >> {marker}\nexit 97\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)


def test_installer_is_executable_and_bash_syntax_is_valid() -> None:
    assert os.access(INSTALLER, os.X_OK), "tools/install-native.sh must be executable"
    result = subprocess.run(
        ["bash", "-n", str(INSTALLER)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_dry_run_changes_nothing_and_reaches_no_network(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    stubs = tmp_path / "stubs"
    marker = tmp_path / "network-called"
    write_network_stubs(stubs, marker)
    before = entries(home)

    result = run_installer(
        "--dry-run",
        "--database-url",
        DATABASE_URL,
        env={
            "HOME": str(home),
            "PATH": f"{stubs}:{os.environ['PATH']}",
            "TMPDIR": str(tmp_path),
        },
    )

    assert result.returncode == 0, result.stderr
    assert "nothing will be changed" in result.stdout
    assert not marker.exists(), f"a dry run invoked: {marker.read_text()}"
    assert entries(home) == before == set(), "a dry run wrote into HOME"
    # The plan names the redacted database URL, never the password.
    assert "ezchain:***@" in result.stdout
    assert SECRET_IN_URL not in result.stdout
    assert SECRET_IN_URL not in result.stderr


def test_dry_run_requires_no_database_and_names_the_reason(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()

    result = run_installer("--dry-run", env={"HOME": str(home), "TMPDIR": str(tmp_path)})

    assert result.returncode == 0, result.stderr
    assert "missing: --database-url" in result.stdout
    assert entries(home) == set()


def test_dry_run_refuses_sqlite_for_a_server_install(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()

    result = run_installer(
        "--dry-run",
        "--database-url",
        f"sqlite:///{tmp_path}/ezchain.db",
        env={"HOME": str(home), "TMPDIR": str(tmp_path)},
    )

    assert result.returncode != 0
    assert "SQLite is not supported" in result.stderr
    assert "0003_paper_fill_order" in result.stderr
    assert not (tmp_path / "ezchain.db").exists()


def test_generated_env_file_is_0600_and_never_echoes_the_token(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()

    result = run_installer(
        "--configure-only",
        "--home",
        str(home / "ezchain"),
        "--database-url",
        DATABASE_URL,
        env={"HOME": str(home), "TMPDIR": str(tmp_path)},
    )

    assert result.returncode == 0, result.stderr
    env_file = home / "ezchain" / "ezchain.env"
    assert env_file.is_file()
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600

    values = read_env(env_file)
    for key in ("EZCHAIN_API_TOKEN", "EZCHAIN_API_KEY_PEPPER"):
        assert len(values[key]) == 64
        assert re.fullmatch(r"[0-9a-f]{64}", values[key])
        assert values[key] not in result.stdout
        assert values[key] not in result.stderr

    assert values["EZCHAIN_DATABASE_URL"] == DATABASE_URL
    assert values["EZCHAIN_EXECUTION_MODE"] == "paper"
    assert values["EZCHAIN_ENVIRONMENT"] == "prod"
    assert values["EZCHAIN_BIND"] == "127.0.0.1"
    assert values["EZCHAIN_ALEMBIC_INI"] == str(home / "ezchain" / "apps" / "api" / "alembic.ini")
    assert SECRET_IN_URL not in result.stdout

    text = env_file.read_text(encoding="utf-8").upper()
    for forbidden in ("MNEMONIC", "PRIVATE_KEY", "PRIVATE KEY", "WALLET", "SIGNING", "VAULT"):
        assert forbidden not in text


def test_configure_only_preserves_values_containing_equals(tmp_path: Path) -> None:
    # The env file is edited line-keyed, not sed-ed. A value that itself contains "="
    # (a percent-encoded password, a query string) must survive a rewrite untouched.
    home = tmp_path / "home"
    home.mkdir()
    target = home / "ezchain"
    target.mkdir()
    env_file = target / "ezchain.env"
    env_file.write_text(
        f"EZCHAIN_API_TOKEN={'a' * 64}\nEZCHAIN_OPERATOR_NOTE=a=b\n",
        encoding="utf-8",
    )

    result = run_installer(
        "--configure-only",
        "--home",
        str(target),
        "--database-url",
        "postgresql+psycopg://ezchain:p%3Dq@127.0.0.1:5432/ezchain",
        env={"HOME": str(home), "TMPDIR": str(tmp_path)},
    )

    assert result.returncode == 0, result.stderr
    values = read_env(env_file)
    assert values["EZCHAIN_OPERATOR_NOTE"] == "a=b"
    assert values["EZCHAIN_API_TOKEN"] == "a" * 64
    assert values["EZCHAIN_DATABASE_URL"] == (
        "postgresql+psycopg://ezchain:p%3Dq@127.0.0.1:5432/ezchain"
    )


def test_configure_only_keeps_an_existing_token_and_refuses_a_short_one(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    target = home / "ezchain"

    first = run_installer(
        "--configure-only",
        "--home",
        str(target),
        "--database-url",
        DATABASE_URL,
        env={"HOME": str(home), "TMPDIR": str(tmp_path)},
    )
    assert first.returncode == 0, first.stderr
    token = read_env(target / "ezchain.env")["EZCHAIN_API_TOKEN"]

    second = run_installer(
        "--configure-only",
        "--home",
        str(target),
        "--database-url",
        DATABASE_URL,
        env={"HOME": str(home), "TMPDIR": str(tmp_path)},
    )
    assert second.returncode == 0, second.stderr
    assert read_env(target / "ezchain.env")["EZCHAIN_API_TOKEN"] == token

    # A pre-existing short secret is an error, not something silently replaced.
    env_file = target / "ezchain.env"
    env_file.write_text("EZCHAIN_API_TOKEN=too-short\n", encoding="utf-8")
    short = run_installer(
        "--configure-only",
        "--home",
        str(target),
        "--database-url",
        DATABASE_URL,
        env={"HOME": str(home), "TMPDIR": str(tmp_path)},
    )
    assert short.returncode != 0
    assert "shorter than 32 characters" in short.stderr
    assert env_file.read_text(encoding="utf-8") == "EZCHAIN_API_TOKEN=too-short\n"


def test_installer_has_no_telemetry_and_no_container_runtime() -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    lowered = text.lower()
    for marker in ("posthog", "segment.io", "sentry", "google-analytics", "mixpanel", "amplitude"):
        assert marker not in lowered
    # The native path never drives Docker, and it never pipes a download into a shell.
    assert "docker " not in lowered
    assert "| sh" not in text
    assert "| bash" not in text


def test_installer_never_names_a_key_store() -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    for marker in ("keystore=", "WALLET_", "MNEMONIC=", "PRIVATE_KEY="):
        assert marker not in text


def test_real_install_refuses_a_private_tmp_hidden_root(tmp_path: Path) -> None:
    # The units set PrivateTmp=yes, so an install root under /tmp is invisible to the
    # service and ReadWritePaths makes systemd exit 226. A real run refuses it up front;
    # --dry-run and --configure-only only warn, since they start no service.
    home = tmp_path / "home"
    home.mkdir()

    result = run_installer(
        "--database-url",
        DATABASE_URL,
        env={"HOME": str(home), "TMPDIR": str(tmp_path)},
    )

    assert result.returncode != 0
    assert "PrivateTmp" in result.stderr
    assert "install root must not be under" in result.stderr
    assert entries(home) == set(), "a refused root must not leave anything behind"


def _prepare_unit_home(tmp_path: Path) -> Path:
    """A HOME in which systemd can resolve every path the units name."""
    home = tmp_path / "unit-home"
    python = home / ".local" / "share" / "ezchain" / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    shutil.copy(shutil.which("true") or "/bin/true", python)
    python.chmod(0o755)
    env_file = home / ".local" / "share" / "ezchain" / "ezchain.env"
    env_file.write_text("EZCHAIN_EXECUTION_MODE=paper\n", encoding="utf-8")
    env_file.chmod(0o600)
    return home


def _unit_directives(path: Path) -> set[str]:
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.strip().startswith("#")
    }


@pytest.mark.parametrize("unit", [API_UNIT, WEB_UNIT])
def test_units_exist_and_declare_their_hardening(unit: Path) -> None:
    assert unit.is_file()
    directives = _unit_directives(unit)

    for required in REQUIRED_SERVICE_DIRECTIVES:
        if required.endswith("="):
            assert any(item.startswith(required) for item in directives), required
        else:
            assert required in directives

    text = unit.read_text(encoding="utf-8")
    assert "%h/.local/share/ezchain" in text, "the installer rewrites this default root"
    assert "127.0.0.1" in text, "the service must bind loopback only"
    assert "[Install]" in text and "WantedBy=default.target" in text
    assert "EnvironmentFile=%h/.local/share/ezchain/ezchain.env" in text


@pytest.mark.parametrize("unit", [API_UNIT, WEB_UNIT])
def test_units_are_valid_for_systemd(unit: Path, tmp_path: Path) -> None:
    analyzer = shutil.which("systemd-analyze")
    if analyzer is None:
        pytest.skip("systemd-analyze is not installed; the directive assertions above apply")

    home = _prepare_unit_home(tmp_path)
    result = subprocess.run(
        [analyzer, "--user", "verify", str(unit)],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home), "TMPDIR": str(tmp_path)},
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_dockerfile_builds_and_installs_the_rust_bridge() -> None:
    # Static assertion only: this environment has no Docker daemon, so the image is
    # validated by reading the file, not by building it.
    text = DOCKERFILE.read_text(encoding="utf-8")

    assert re.search(r"^FROM rust:\S+@sha256:[0-9a-f]{64} AS bridge$", text, re.MULTILINE), (
        "the bridge stage must be a digest-pinned rust image"
    )
    assert "COPY rust ./rust" in text
    assert "cargo build --locked --release -p ezchain-bridge" in text

    install = re.search(
        r"^COPY --from=bridge \S*libezchain_bridge\.so "
        r"/usr/local/lib/python3\.11/site-packages/ezchain_bridge\.so$",
        text,
        re.MULTILINE,
    )
    assert install is not None, "the runtime image must install ezchain_bridge.so"

    # The digest is documented with the tag it came from, as tools/check_pins.py requires.
    digest = re.search(r"^FROM rust:\S+@sha256:([0-9a-f]{64}) AS bridge$", text, re.MULTILINE)
    assert digest is not None
    assert f"rust:1.100-slim-bookworm -> sha256:{digest.group(1)}" in text

    # The bridge stage must come before the runtime stage that copies from it.
    assert text.index("AS bridge") < text.index("AS runtime")
