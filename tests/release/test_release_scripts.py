"""The release scripts sign artifacts without ever handling key material in the repository.

Every test generates a throwaway key pair in a temporary directory. No fixture carries a
private key, and the scripts are exercised as the operator runs them: through the shell.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SIGN = ROOT / "tools/release/sign.sh"
VERIFY = ROOT / "tools/release/verify.sh"
PACKAGE = ROOT / "tools/install/package-release.sh"

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl is required")


def _run(*command: object, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(part) for part in command],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _key_pair(directory: Path) -> tuple[Path, Path]:
    private = directory / "release-private.pem"
    public = directory / "release-public.pem"
    subprocess.run(
        [
            "openssl",
            "genpkey",
            "-algorithm",
            "RSA",
            "-pkeyopt",
            "rsa_keygen_bits:2048",
            "-out",
            str(private),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)],
        check=True,
        capture_output=True,
    )
    return private, public


def _env(private: Path, **extra: str) -> dict[str, str]:
    import os

    env = dict(os.environ)
    env["EZCHAIN_SIGNING_KEY"] = str(private)
    env.update(extra)
    return env


def test_sign_and_verify_roundtrip(tmp_path: Path) -> None:
    private, public = _key_pair(tmp_path)
    artifact = tmp_path / "ezchain-test.tar.gz.sha256"
    artifact.write_text("deadbeef  ezchain-test.tar.gz\n", encoding="utf-8")

    signed = _run(SIGN, "--yes", artifact, env=_env(private))
    assert signed.returncode == 0, signed.stderr
    assert (tmp_path / "ezchain-test.tar.gz.sha256.sig").is_file()

    checked = _run(VERIFY, "--public-key", public, artifact)
    assert checked.returncode == 0, checked.stderr
    assert "verified" in checked.stdout


def test_verify_rejects_a_tampered_artifact(tmp_path: Path) -> None:
    private, public = _key_pair(tmp_path)
    artifact = tmp_path / "checksum.sha256"
    artifact.write_text("good\n", encoding="utf-8")
    assert _run(SIGN, "--yes", artifact, env=_env(private)).returncode == 0

    artifact.write_text("tampered\n", encoding="utf-8")
    checked = _run(VERIFY, "--public-key", public, artifact)
    assert checked.returncode != 0
    assert "did not verify" in checked.stderr


def test_verify_fails_closed_without_a_public_key(tmp_path: Path) -> None:
    artifact = tmp_path / "checksum.sha256"
    artifact.write_text("good\n", encoding="utf-8")
    (tmp_path / "checksum.sha256.sig").write_bytes(b"not a signature")

    checked = _run(VERIFY, "--public-key", tmp_path / "missing.pem", artifact)
    assert checked.returncode != 0


def test_sign_refuses_without_confirmation_or_key(tmp_path: Path) -> None:
    private, _ = _key_pair(tmp_path)
    artifact = tmp_path / "checksum.sha256"
    artifact.write_text("good\n", encoding="utf-8")

    import os

    no_yes = _run(SIGN, artifact, env=_env(private))
    assert no_yes.returncode != 0
    assert not (tmp_path / "checksum.sha256.sig").exists()

    bare = dict(os.environ)
    bare.pop("EZCHAIN_SIGNING_KEY", None)
    no_key = _run(SIGN, "--yes", artifact, env=bare)
    assert no_key.returncode != 0
    assert not (tmp_path / "checksum.sha256.sig").exists()


def test_sign_never_prints_key_material(tmp_path: Path) -> None:
    private, _ = _key_pair(tmp_path)
    artifact = tmp_path / "checksum.sha256"
    artifact.write_text("good\n", encoding="utf-8")
    secret = private.read_text(encoding="utf-8")

    signed = _run(SIGN, "--yes", artifact, env=_env(private))
    assert signed.returncode == 0, signed.stderr
    assert "PRIVATE KEY" not in signed.stdout + signed.stderr
    assert secret not in signed.stdout + signed.stderr
    assert str(private) not in signed.stdout + signed.stderr


def test_package_release_produces_a_signed_checksum(tmp_path: Path) -> None:
    private, public = _key_pair(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("a tiny source tree\n", encoding="utf-8")
    out = tmp_path / "dist"

    packed = _run(
        PACKAGE,
        "--tag",
        "v0.0.0-test",
        "--source",
        source,
        "--out",
        out,
        "--worktree",
        "--sign",
        env=_env(private),
    )
    assert packed.returncode == 0, packed.stderr

    checksum = out / "ezchain-v0.0.0-test.tar.gz.sha256"
    assert checksum.is_file()
    assert (out / "ezchain-v0.0.0-test.tar.gz").is_file()
    signature = out / "ezchain-v0.0.0-test.tar.gz.sha256.sig"
    assert signature.is_file()

    checked = _run(VERIFY, "--public-key", public, checksum)
    assert checked.returncode == 0, checked.stderr
