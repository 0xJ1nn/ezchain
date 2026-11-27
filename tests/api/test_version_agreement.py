"""VERSION is the one product version.

The API probes, the web footer and the package manifests are all derived from the
repository-root ``VERSION`` file. This module parses the on-disk sources so a change to
any one of them, without the others, fails the suite.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ezchain_api.routers import system

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "apps" / "web"
VERSION_FILE = ROOT / "VERSION"
_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


def _product_version() -> str:
    """The authoritative value, parsed with the same strict shape the readers use."""
    raw = VERSION_FILE.read_text(encoding="utf-8").strip()
    assert _VERSION_PATTERN.fullmatch(raw), f"VERSION is not a semantic version: {raw!r}"
    return raw


def _manifest_version(path: Path) -> str | None:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    project = data.get("project", {})
    value = project.get("version")
    assert value is None or isinstance(value, str)
    return value


def _web_product_version(extra_args: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    """Execute the same reader the Next build uses, with no network and no dependencies."""
    expression = (
        "import { pathToFileURL } from 'node:url';"
        "const m = await import(pathToFileURL(process.argv[1]).href);"
        "process.stdout.write(m.readProductVersion(process.argv[2]));"
    )
    return subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            expression,
            str(WEB / "scripts" / "product-version.mjs"),
            *(extra_args or [str(WEB)]),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_product_version_file_is_the_single_semantic_version() -> None:
    assert VERSION_FILE.read_text(encoding="utf-8").splitlines() == ["0.1.0"]
    assert _product_version() == "0.1.0"


def test_health_and_healthz_report_the_same_version(
    client_for: Callable[[object], TestClient],
) -> None:
    """Both probes answer with VERSION and keep their existing fields."""
    client = client_for(system.router)
    expected = _product_version()

    health = client.get("/health")
    healthz = client.get("/healthz")

    assert health.status_code == 200
    assert healthz.status_code == 200
    assert health.json()["version"] == expected
    assert healthz.json()["version"] == expected
    assert set(health.json()) >= {"status", "db", "mode", "version"}
    assert set(healthz.json()) >= {"status", "mode", "version"}


def test_openapi_version_matches_the_product_version() -> None:
    app = FastAPI()
    app.include_router(system.router)
    assert app.openapi()["info"]["version"] == _product_version()


def test_footer_version_is_read_from_the_same_file() -> None:
    """The footer value is produced by the web reader, not a second hard-coded string."""
    expected = _product_version()
    result = _web_product_version()
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected

    config = (WEB / "next.config.ts").read_text(encoding="utf-8")
    assert "readProductVersion" in config
    assert "EZCHAIN_PRODUCT_VERSION" in config
    assert "throw new Error" in config  # an unreadable VERSION fails the build

    shell = (WEB / "components" / "shell.tsx").read_text(encoding="utf-8")
    assert "PRODUCT_VERSION" in shell
    assert "version={PRODUCT_VERSION}" in shell

    sidebar = (WEB / "components" / "sidebar.tsx").read_text(encoding="utf-8")
    assert 'data-testid="product-version"' in sidebar
    assert "v{version}" in sidebar
    assert not _VERSION_PATTERN.search(sidebar), "the footer must not hard-code a version"

    product_version_module = (WEB / "lib" / "product-version.ts").read_text(encoding="utf-8")
    assert "process.env.EZCHAIN_PRODUCT_VERSION" in product_version_module
    assert not _VERSION_PATTERN.search(product_version_module)


def test_web_reader_refuses_a_missing_version(tmp_path: Path) -> None:
    result = _web_product_version([str(tmp_path / "no-version-here")])
    assert result.returncode != 0
    assert "product version" in result.stderr
    assert result.stdout == ""


def test_python_reader_refuses_a_missing_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import importlib

    import ezchain_api.version as version_module

    monkeypatch.setenv("EZCHAIN_VERSION_FILE", str(tmp_path / "absent"))
    try:
        with pytest.raises(RuntimeError, match="product version"):
            importlib.reload(version_module)
    finally:
        monkeypatch.delenv("EZCHAIN_VERSION_FILE")
        importlib.reload(version_module)


def test_package_manifests_follow_the_documented_version_rule() -> None:
    """Root has no product version; package versions are pinned and documented."""
    root_pyproject = ROOT / "pyproject.toml"
    assert _manifest_version(root_pyproject) is None

    expected: dict[str, str | None] = {
        "packages/core-py/pyproject.toml": _product_version(),
        "packages/adapters/pyproject.toml": _product_version(),
        # Package-only versions, documented in docs/VERSIONING.md.
        "packages/sdk-py/pyproject.toml": "1.0.0",
        "apps/web/package.json": "0.0.0",
    }
    observed: dict[str, str | None] = {}
    for relative, value in expected.items():
        path = ROOT / relative
        if path.suffix == ".json":
            observed[relative] = json.loads(path.read_text(encoding="utf-8")).get("version")
        else:
            observed[relative] = _manifest_version(path)
    assert observed == expected

    doc = (ROOT / "docs" / "VERSIONING.md").read_text(encoding="utf-8")
    assert "packages/sdk-py" in doc and "1.0.0" in doc
    assert "apps/web" in doc and "0.0.0" in doc
    assert "VERSION" in doc
