"""The build-source flag verifies an attestation and fails closed with a reason.

The flag is an honest statement about provenance: a valid signature over an attestation
that names the official repository is the only path to a positive result. A source tree, a
fork or a missing key each reports its own reason instead of claiming to be official.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ezchain_api.routers.system import OFFICIAL_REPOSITORY, build_source_status

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl is required")


def _key_pair(directory: Path) -> tuple[Path, Path]:
    private = directory / "private.pem"
    public = directory / "public.pem"
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


def _attestation(
    directory: Path, *, repository: str = OFFICIAL_REPOSITORY, tag: str = "v0.2.0"
) -> Path:
    path = directory / "attestation.json"
    path.write_text(json.dumps({"repository": repository, "tag": tag}), encoding="utf-8")
    return path


def _sign(private: Path, target: Path) -> Path:
    signature = Path(f"{target}.sig")
    subprocess.run(
        [
            "openssl",
            "dgst",
            "-sha256",
            "-sign",
            str(private),
            "-out",
            str(signature),
            str(target),
        ],
        check=True,
        capture_output=True,
    )
    return signature


def _configure(monkeypatch: pytest.MonkeyPatch, public: Path, attestation: Path) -> None:
    monkeypatch.setenv("EZCHAIN_RELEASE_PUBLIC_KEY", str(public))
    monkeypatch.setenv("EZCHAIN_BUILD_ATTESTATION", str(attestation))


def test_a_valid_official_attestation_reports_official(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private, public = _key_pair(tmp_path)
    attestation = _attestation(tmp_path)
    _sign(private, attestation)
    _configure(monkeypatch, public, attestation)

    result = build_source_status()
    assert result.official is True
    assert result.status == "verified"
    assert result.tag == "v0.2.0"
    assert "PRIVATE KEY" not in result.model_dump_json()


def test_a_tampered_attestation_is_not_official(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private, public = _key_pair(tmp_path)
    attestation = _attestation(tmp_path)
    _sign(private, attestation)
    attestation.write_text(
        json.dumps({"repository": OFFICIAL_REPOSITORY, "tag": "v9.9.9"}), encoding="utf-8"
    )
    _configure(monkeypatch, public, attestation)

    result = build_source_status()
    assert result.official is False
    assert result.status == "invalid_signature"
    assert "does not verify" in result.message


def test_a_missing_public_key_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    attestation = _attestation(tmp_path)
    _configure(monkeypatch, tmp_path / "no-key.pem", attestation)

    result = build_source_status()
    assert result.official is False
    assert result.status == "no_public_key"


def test_a_missing_attestation_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, public = _key_pair(tmp_path)
    _configure(monkeypatch, public, tmp_path / "no-attestation.json")

    result = build_source_status()
    assert result.official is False
    assert result.status == "no_attestation"


def test_a_valid_but_fork_attestation_is_not_official(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private, public = _key_pair(tmp_path)
    attestation = _attestation(tmp_path, repository="https://example.test/fork")
    _sign(private, attestation)
    _configure(monkeypatch, public, attestation)

    result = build_source_status()
    assert result.official is False
    assert result.status == "repository_mismatch"


def test_a_malformed_attestation_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private, public = _key_pair(tmp_path)
    attestation = tmp_path / "attestation.json"
    attestation.write_text("not json", encoding="utf-8")
    _sign(private, attestation)
    _configure(monkeypatch, public, attestation)

    result = build_source_status()
    assert result.official is False
    assert result.status == "malformed_attestation"


def test_the_endpoint_reports_the_same_verdict(
    tmp_path: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    private, public = _key_pair(tmp_path)
    attestation = _attestation(tmp_path)
    _sign(private, attestation)
    _configure(monkeypatch, public, attestation)

    response = client.get("/api/v1/system/build-source")
    assert response.status_code == 200
    body = response.json()
    assert body["official"] is True
    assert body["status"] == "verified"
    assert "PRIVATE KEY" not in response.text
