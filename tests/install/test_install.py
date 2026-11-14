"""Installer contract tests that run without a Docker daemon.

The clean-container run lives in ``tests/install/test_container_install.sh``. These tests
cover everything that can be proven on a normal host: checksum refusal, extraction safety,
secret generation and rerun idempotence, the release packager, and the edge's operational
surface.
"""

from __future__ import annotations

import functools
import hashlib
import io
import os
import stat
import subprocess
import tarfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "tools" / "install" / "install.sh"
PACKAGER = ROOT / "tools" / "install" / "package-release.sh"
CADDYFILE = ROOT / "infra" / "Caddyfile"
TAG = "v0.0.1-test"

REQUIRED_FILES = (
    "infra/compose.yaml",
    "infra/Caddyfile",
    "apps/api/Dockerfile",
    "apps/web/Dockerfile",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_release(tmp_path: Path, *, prefix: str | None = TAG) -> tuple[Path, str]:
    source = tmp_path / "source"
    root = source / (prefix or "release")
    for relative in REQUIRED_FILES:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# fixture\n", encoding="utf-8")

    archive = tmp_path / f"ezchain-{TAG}.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for child in sorted(root.rglob("*")):
            bundle.add(child, arcname=str(child.relative_to(source)))
    return archive, sha256(archive)


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


def install(tmp_path: Path, archive: Path, digest: str, *extra: str) -> Path:
    target = tmp_path / "install"
    result = run_installer(
        "--version",
        TAG,
        "--archive",
        str(archive),
        "--sha256",
        digest,
        "--dir",
        str(target),
        "--no-start",
        *extra,
    )
    assert result.returncode == 0, result.stderr
    return target


def test_dry_run_changes_nothing(tmp_path: Path) -> None:
    target = tmp_path / "install"
    result = run_installer("--dry-run", "--version", TAG, "--dir", str(target))

    assert result.returncode == 0, result.stderr
    assert "nothing will be changed" in result.stdout
    assert not target.exists()


def test_checksum_mismatch_refuses_and_writes_no_secret(tmp_path: Path) -> None:
    archive, _ = make_release(tmp_path)
    target = tmp_path / "install"

    result = run_installer(
        "--version",
        TAG,
        "--archive",
        str(archive),
        "--sha256",
        "0" * 64,
        "--dir",
        str(target),
        "--no-start",
    )

    assert result.returncode != 0
    assert "SHA-256 mismatch" in result.stderr
    assert not (target / "ezchain.env").exists()


def test_archive_requires_a_checksum(tmp_path: Path) -> None:
    archive, _ = make_release(tmp_path)
    result = run_installer(
        "--version",
        TAG,
        "--archive",
        str(archive),
        "--dir",
        str(tmp_path / "install"),
        "--no-start",
    )

    assert result.returncode != 0
    assert "--archive requires --sha256" in result.stderr


def test_install_generates_only_deployment_secrets(tmp_path: Path) -> None:
    archive, digest = make_release(tmp_path)
    target = install(tmp_path, archive, digest)

    env_file = target / "ezchain.env"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    values = read_env(env_file)

    for key in ("POSTGRES_PASSWORD", "EZCHAIN_API_TOKEN", "EZCHAIN_API_KEY_PEPPER"):
        assert len(values[key]) == 64
        assert all(character in "0123456789abcdef" for character in values[key])

    assert values["EZCHAIN_EXECUTION_MODE"] == "paper"
    assert values["EZCHAIN_DATA_MODE"] == "live"
    assert values["EZCHAIN_ENVIRONMENT"] == "prod"
    assert values["EZCHAIN_BIND"] == "127.0.0.1"
    assert values["EZCHAIN_VERSION"] == TAG

    text = env_file.read_text(encoding="utf-8").upper()
    for forbidden in ("MNEMONIC", "PRIVATE KEY", "PRIVATE_KEY", "WALLET", "SIGNING", "VAULT"):
        assert forbidden not in text

    assert (target / "current" / "infra" / "compose.yaml").is_file()
    assert (target / "current" / "infra" / ".env").resolve() == env_file.resolve()


def test_rerun_keeps_secrets_and_updates_configuration(tmp_path: Path) -> None:
    archive, digest = make_release(tmp_path)
    target = install(tmp_path, archive, digest)
    first = read_env(target / "ezchain.env")

    second = install(tmp_path, archive, digest, "--public-url", "http://example.test:9090")
    values = read_env(second / "ezchain.env")

    for key in ("POSTGRES_PASSWORD", "EZCHAIN_API_TOKEN", "EZCHAIN_API_KEY_PEPPER"):
        assert values[key] == first[key]
    assert values["EZCHAIN_PUBLIC_URL"] == "http://example.test:9090"


def test_existing_short_token_is_not_silently_replaced(tmp_path: Path) -> None:
    archive, digest = make_release(tmp_path)
    target = tmp_path / "install"
    target.mkdir()
    env_file = target / "ezchain.env"
    env_file.write_text("EZCHAIN_API_TOKEN=too-short\n", encoding="utf-8")

    result = run_installer(
        "--version",
        TAG,
        "--archive",
        str(archive),
        "--sha256",
        digest,
        "--dir",
        str(target),
        "--no-start",
    )

    assert result.returncode != 0
    assert "shorter than 32 characters" in result.stderr
    assert env_file.read_text(encoding="utf-8") == "EZCHAIN_API_TOKEN=too-short\n"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass


def serve_release(tmp_path: Path) -> tuple[ThreadingHTTPServer, str]:
    handler = functools.partial(_QuietHandler, directory=str(tmp_path / "releases"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def install_from_publication(tmp_path: Path, base: str) -> subprocess.CompletedProcess[str]:
    return run_installer(
        "--version",
        TAG,
        "--base-url",
        base,
        "--dir",
        str(tmp_path / "install"),
        "--no-start",
        env={"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"},
    )


def test_downloaded_release_is_verified_against_its_checksum(tmp_path: Path) -> None:
    archive, digest = make_release(tmp_path)
    published = tmp_path / "releases" / TAG
    published.mkdir(parents=True)
    name = f"ezchain-{TAG}.tar.gz"
    (published / name).write_bytes(archive.read_bytes())
    (published / f"{name}.sha256").write_text(f"{digest}  {name}\n", encoding="utf-8")

    server, base = serve_release(tmp_path)
    try:
        target = tmp_path / "install"
        result = install_from_publication(tmp_path, base)
    finally:
        server.shutdown()
        server.server_close()

    assert result.returncode == 0, result.stderr
    assert f"verified SHA-256 {digest}" in result.stdout
    assert (target / "current" / "infra" / "compose.yaml").is_file()


def publish(tmp_path: Path, archive: Path, checksum_text: str) -> None:
    published = tmp_path / "releases" / TAG
    published.mkdir(parents=True, exist_ok=True)
    name = f"ezchain-{TAG}.tar.gz"
    (published / name).write_bytes(archive.read_bytes())
    (published / f"{name}.sha256").write_text(checksum_text, encoding="utf-8")


def test_checksum_naming_a_different_file_is_refused(tmp_path: Path) -> None:
    archive, digest = make_release(tmp_path)
    publish(tmp_path, archive, f"{digest}  ezchain-other.tar.gz\n")

    server, base = serve_release(tmp_path)
    try:
        result = install_from_publication(tmp_path, base)
    finally:
        server.shutdown()
        server.server_close()

    assert result.returncode != 0
    assert "does not name" in result.stderr
    assert not (tmp_path / "install" / "ezchain.env").exists()


def test_checksum_without_a_bound_filename_is_refused(tmp_path: Path) -> None:
    archive, digest = make_release(tmp_path)
    publish(tmp_path, archive, f"{digest}\n")

    server, base = serve_release(tmp_path)
    try:
        result = install_from_publication(tmp_path, base)
    finally:
        server.shutdown()
        server.server_close()

    assert result.returncode != 0
    assert "does not name" in result.stderr


def test_checksum_with_a_binary_marker_names_the_archive(tmp_path: Path) -> None:
    archive, digest = make_release(tmp_path)
    name = f"ezchain-{TAG}.tar.gz"
    publish(tmp_path, archive, f"{digest} *{name}\n")

    server, base = serve_release(tmp_path)
    try:
        result = install_from_publication(tmp_path, base)
    finally:
        server.shutdown()
        server.server_close()

    assert result.returncode == 0, result.stderr


def test_archive_with_parent_traversal_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        payload = b"escape\n"
        member = tarfile.TarInfo("../escape.txt")
        member.size = len(payload)
        bundle.addfile(member, io.BytesIO(payload))
    digest = sha256(archive)

    result = run_installer(
        "--version",
        TAG,
        "--archive",
        str(archive),
        "--sha256",
        digest,
        "--dir",
        str(tmp_path / "install"),
        "--no-start",
    )

    assert result.returncode != 0
    assert "unsafe path" in result.stderr
    assert not (tmp_path / "escape.txt").exists()


def test_archive_with_a_symlink_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "linked.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for relative in REQUIRED_FILES:
            payload = b"# fixture\n"
            member = tarfile.TarInfo(relative)
            member.size = len(payload)
            bundle.addfile(member, io.BytesIO(payload))
        link = tarfile.TarInfo("infra/escape")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        bundle.addfile(link)
    digest = sha256(archive)

    result = run_installer(
        "--version",
        TAG,
        "--archive",
        str(archive),
        "--sha256",
        digest,
        "--dir",
        str(tmp_path / "install"),
        "--no-start",
    )

    assert result.returncode != 0
    assert "link or special file" in result.stderr


def test_edge_does_not_publish_operational_endpoints() -> None:
    text = CADDYFILE.read_text(encoding="utf-8")

    assert "@operational path /metrics /health /docs /docs/* /redoc /redoc/* /openapi.json" in text
    assert "respond 404" in text
    assert "handle /healthz" in text
    assert "handle /readyz" in text


def test_compose_keeps_operational_ports_on_loopback() -> None:
    text = (ROOT / "infra" / "compose.yaml").read_text(encoding="utf-8")

    assert '"127.0.0.1:9090:9090"' in text
    assert '"127.0.0.1:3001:3000"' in text
    assert 'EZCHAIN_EXECUTION_MODE: "${EZCHAIN_EXECUTION_MODE:-paper}"' in text


def test_installer_has_no_telemetry() -> None:
    text = INSTALLER.read_text(encoding="utf-8").lower()

    for marker in ("posthog", "segment.io", "sentry", "google-analytics", "mixpanel", "amplitude"):
        assert marker not in text


def test_package_release_produces_a_verifiable_pair(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    for relative in REQUIRED_FILES:
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# fixture\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Installer Test",
            "-c",
            "user.email=installer@example.test",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=repository,
        check=True,
    )
    subprocess.run(["git", "tag", TAG], cwd=repository, check=True)

    out = tmp_path / "dist"
    result = subprocess.run(
        [
            "bash",
            str(PACKAGER),
            "--tag",
            TAG,
            "--source",
            str(repository),
            "--out",
            str(out),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    archive = out / f"ezchain-{TAG}.tar.gz"
    checksum = out / f"ezchain-{TAG}.tar.gz.sha256"
    assert archive.is_file()
    assert checksum.read_text(encoding="utf-8").split()[0] == sha256(archive)

    # The installer must accept the pair the packager produces.
    target = tmp_path / "installed"
    installed = run_installer(
        "--version",
        TAG,
        "--archive",
        str(archive),
        "--sha256",
        checksum.read_text(encoding="utf-8").split()[0],
        "--dir",
        str(target),
        "--no-start",
    )
    assert installed.returncode == 0, installed.stderr
    assert (target / "current" / "infra" / "compose.yaml").is_file()


def test_worktree_package_excludes_local_environment_files(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    for relative in REQUIRED_FILES:
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# fixture\n", encoding="utf-8")
    (repository / ".env").write_text("POSTGRES_PASSWORD=local-only\n", encoding="utf-8")
    (repository / "infra" / ".env").write_text("EZCHAIN_API_TOKEN=local-only\n", encoding="utf-8")

    out = tmp_path / "dist"
    result = subprocess.run(
        [
            "bash",
            str(PACKAGER),
            "--worktree",
            "--tag",
            TAG,
            "--source",
            str(repository),
            "--out",
            str(out),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    with tarfile.open(out / f"ezchain-{TAG}.tar.gz") as archive:
        names = archive.getnames()
    assert all(Path(name).name != ".env" for name in names)


def test_installer_is_executable_and_bash_syntax_is_valid() -> None:
    assert os.access(INSTALLER, os.X_OK)
    for script in (INSTALLER, PACKAGER):
        result = subprocess.run(
            ["bash", "-n", str(script)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script", [INSTALLER, PACKAGER])
def test_scripts_do_not_pipe_a_download_into_a_shell(script: Path) -> None:
    text = script.read_text(encoding="utf-8")
    assert "| sh" not in text
    assert "| bash" not in text


def test_installer_does_not_reference_a_key_store() -> None:
    # The installer may talk about keys in its comments; it must never name a path or
    # variable it could write key material to.
    text = INSTALLER.read_text(encoding="utf-8")
    for marker in ("keystore=", "WALLET_", "MNEMONIC=", "PRIVATE_KEY="):
        assert marker not in text


def test_installer_does_not_create_a_keys_directory(tmp_path: Path) -> None:
    archive, digest = make_release(tmp_path)
    target = install(tmp_path, archive, digest)
    names = {path.name.lower() for path in target.rglob("*")}
    assert "keys" not in names
    assert "vault" not in names
