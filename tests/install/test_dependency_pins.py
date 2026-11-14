"""The pins are exact, hashed, reproducible, and a bump fails the check.

The check script is exercised against a synthetic repository in a temporary
directory, so every failure path is a real run of the same code CI uses rather
than a mock of it. The real locks are checked too: a committed range that the
lock no longer satisfies fails here exactly as it fails in the gate.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from tools import audit_pins, check_pins

ROOT = Path(__file__).resolve().parents[2]


def _hashed(pins: dict[str, str], *, digest: str = "a" * 64) -> str:
    lines = []
    for name, version in pins.items():
        lines.append(f"{name}=={version} \\")
        lines.append(f"    --hash=sha256:{digest} \\")
        lines.append(f"    --hash=sha256:{digest[::-1]}")
        lines.append("    # via -r requirements.txt")
    return "\n".join(lines) + "\n"


def _dockerfile(digest: str = "b" * 64, *, tag: str = "python:3.11-slim") -> str:
    return (
        f"# {tag} -> sha256:{digest}\n"
        f"FROM {tag}@sha256:{digest} AS builder\n"
        "RUN echo build\n"
        f"# {tag} -> sha256:{digest}\n"
        f"FROM {tag}@sha256:{digest} AS runtime\n"
    )


def _repository(
    tmp_path: Path, *, dev: dict[str, str], runtime: dict[str, str] | None = None
) -> Path:
    """A minimal checkout: the three inputs, their locks, the Node lock and two images."""
    root = tmp_path / "repo"
    (root / "apps" / "api").mkdir(parents=True)
    (root / "apps" / "web").mkdir(parents=True)
    (root / "packages" / "core-py").mkdir(parents=True)
    (root / "packages" / "core-py" / "pyproject.toml").write_text(
        '[project]\nname = "ezchain-core"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (root / "apps" / "api" / "Dockerfile").write_text(_dockerfile(), encoding="utf-8")
    (root / "apps" / "web" / "Dockerfile").write_text(
        _dockerfile("c" * 64, tag="node:22-alpine"), encoding="utf-8"
    )
    (root / "requirements.txt").write_text(
        "".join(f"{name}>={version.split('.')[0]}\n" for name, version in dev.items()),
        encoding="utf-8",
    )
    (root / "requirements-runtime.txt").write_text(
        "".join(f"{name}>={version.split('.')[0]}\n" for name, version in (runtime or dev).items()),
        encoding="utf-8",
    )
    (root / "requirements.lock").write_text(_hashed(dev), encoding="utf-8")
    (root / "requirements-runtime.lock").write_text(_hashed(runtime or dev), encoding="utf-8")
    (root / "requirements-keyring.lock").write_text(
        _hashed({"keyring": "25.7.0"}), encoding="utf-8"
    )
    (root / "apps" / "api" / "pyproject.toml").write_text(
        '[project]\nname = "ezchain-api"\nversion = "0.1.0"\n'
        '[project.optional-dependencies]\nkeyring = ["keyring>=24,<26"]\n',
        encoding="utf-8",
    )
    (root / "pnpm-lock.yaml").write_text(
        "lockfileVersion: '9.0'\n\npackages:\n\n"
        "  postcss@8.5.33:\n"
        "    resolution: {integrity: sha512-" + "a" * 64 + "}\n",
        encoding="utf-8",
    )
    return root


def test_the_committed_pins_are_exact_hashed_and_cover_their_sources() -> None:
    assert check_pins.check(ROOT) == []


@pytest.mark.parametrize("name", sorted(check_pins.SOURCES))
def test_every_committed_pin_carries_an_exact_version_and_hashes(name: str) -> None:
    pins = check_pins.parse_lock(ROOT / name)

    assert pins, f"{name} has no pins"
    for pin in pins.values():
        assert pin.version, f"{name}: {pin.name} has no version"
        assert pin.version[0].isdigit(), f"{name}: {pin.name} is not an exact version"
        assert pin.hashes, f"{name}: {pin.name} carries no sha256 hash"


def test_the_runtime_pins_are_the_dev_pins_at_the_same_versions() -> None:
    dev = check_pins.parse_lock(ROOT / "requirements.lock")
    runtime = check_pins.parse_lock(ROOT / "requirements-runtime.lock")

    assert {name: pin.version for name, pin in runtime.items()} == {
        name: dev[name].version for name in runtime
    }


def test_the_node_lock_has_an_integrity_hash_for_every_resolution() -> None:
    errors: list[str] = []
    check_pins._check_pnpm(ROOT, errors)

    assert errors == []


def test_the_checks_pass_on_a_consistent_minimal_repository(tmp_path: Path) -> None:
    root = _repository(tmp_path, dev={"fastapi": "0.162.7", "httpx": "0.28.1"})

    assert check_pins.check(root) == []


def test_a_base_image_without_a_digest_fails_the_check(tmp_path: Path) -> None:
    root = _repository(tmp_path, dev={"fastapi": "0.162.7"})
    (root / "apps" / "api" / "Dockerfile").write_text(
        "FROM python:3.11-slim AS builder\n", encoding="utf-8"
    )

    errors = check_pins.check(root)

    assert errors and "python:3.11-slim is not pinned by sha256 digest" in errors[0]


def test_a_digest_without_its_tag_comment_fails_the_check(tmp_path: Path) -> None:
    root = _repository(tmp_path, dev={"fastapi": "0.162.7"})
    (root / "apps" / "api" / "Dockerfile").write_text(
        f"FROM python:3.11-slim@sha256:{'b' * 64} AS builder\n", encoding="utf-8"
    )

    errors = check_pins.check(root)

    assert errors and "no comment records the tag" in errors[0]


def test_a_stage_reference_is_not_treated_as_an_image(tmp_path: Path) -> None:
    root = _repository(tmp_path, dev={"fastapi": "0.162.7"})
    (root / "apps" / "api" / "Dockerfile").write_text(
        _dockerfile() + "FROM builder AS second\n", encoding="utf-8"
    )

    assert check_pins.check(root) == []


def test_a_declared_dependency_no_lock_pins_fails_the_check(tmp_path: Path) -> None:
    root = _repository(tmp_path, dev={"fastapi": "0.162.7"})
    (root / "packages" / "core-py" / "pyproject.toml").write_text(
        '[project]\nname = "ezchain-core"\nversion = "0.1.0"\ndependencies = ["arrow>=1"]\n',
        encoding="utf-8",
    )

    errors = check_pins.check(root)

    assert errors == [
        "packages/core-py/pyproject.toml[dependencies]: arrow is declared but no lock pins it"
    ]


def test_a_url_requirement_needs_a_written_reason(tmp_path: Path) -> None:
    root = _repository(tmp_path, dev={"fastapi": "0.162.7"})
    (root / "packages" / "core-py" / "pyproject.toml").write_text(
        '[project]\nname = "ezchain-core"\nversion = "0.1.0"\n'
        '[project.optional-dependencies]\nfirehose = ["stream @ git+https://x.invalid/s.git@v1"]\n',
        encoding="utf-8",
    )

    errors = check_pins.check(root)

    assert errors and "is a URL requirement with no hash" in errors[0]

    unpinned = root / "tools" / "pins" / "unpinned.txt"
    unpinned.parent.mkdir(parents=True)
    unpinned.write_text("stream v1 has no published artifact, so no sha256 exists\n")
    assert check_pins.check(root) == []


def test_an_unpinned_entry_without_a_reason_is_refused(tmp_path: Path) -> None:
    unpinned = tmp_path / "unpinned.txt"
    unpinned.write_text("# comment\nstream\n", encoding="utf-8")

    with pytest.raises(check_pins.PinsError, match="needs a name and a reason"):
        check_pins.load_unpinned(unpinned)


def test_the_committed_dockerfiles_pin_every_base_image_by_digest() -> None:
    errors: list[str] = []

    check_pins._check_dockerfiles(ROOT, errors)

    assert errors == []
    for relative in check_pins.DOCKERFILES:
        images = check_pins.base_images(ROOT / relative)
        assert images, f"{relative} has no base image"
        for number, image in images:
            assert re.fullmatch(r"\S+@sha256:[0-9a-f]{64}", image), (
                f"{relative}:{number}: {image!r} is not pinned by digest"
            )


def test_a_bumped_constraint_fails_the_check(tmp_path: Path) -> None:
    root = _repository(tmp_path, dev={"fastapi": "0.162.7"})
    (root / "requirements.txt").write_text("fastapi>=0.200,<1\n", encoding="utf-8")

    errors = check_pins.check(root)

    assert len(errors) == 1
    assert errors[0].startswith("requirements.lock: fastapi==0.162.7 does not satisfy")
    assert check_pins.fast(root) == 1


def test_a_new_requirement_without_a_pin_fails_the_check(tmp_path: Path) -> None:
    root = _repository(tmp_path, dev={"fastapi": "0.162.7"})
    (root / "requirements.txt").write_text("fastapi>=0.1\nhttpx>=0.27\n", encoding="utf-8")

    assert check_pins.check(root) == ["requirements.lock: httpx is required but not pinned"]


def test_a_range_in_a_lock_is_refused(tmp_path: Path) -> None:
    root = _repository(tmp_path, dev={"fastapi": "0.162.7"})
    (root / "requirements.lock").write_text(
        "fastapi>=0.162 \\\n    --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8"
    )

    errors = check_pins.check(root)

    assert errors and "is not an exact pin" in errors[0]


def test_an_unhashed_pin_is_refused(tmp_path: Path) -> None:
    root = _repository(tmp_path, dev={"fastapi": "0.162.7"})
    (root / "requirements.lock").write_text("fastapi==0.162.7\n", encoding="utf-8")

    assert "requirements.lock: fastapi==0.162.7 carries no sha256 hash" in check_pins.check(root)


def test_a_runtime_pin_that_drifted_from_the_dev_pin_is_refused(tmp_path: Path) -> None:
    root = _repository(tmp_path, dev={"fastapi": "0.162.7"})
    (root / "requirements-runtime.lock").write_text(
        _hashed({"fastapi": "0.162.8"}), encoding="utf-8"
    )

    assert "requirements-runtime.lock: fastapi==0.162.8 but requirements.lock pins 0.162.7" in (
        check_pins.check(root)
    )


def test_a_node_resolution_without_integrity_is_refused(tmp_path: Path) -> None:
    root = _repository(tmp_path, dev={"fastapi": "0.162.7"})
    (root / "pnpm-lock.yaml").write_text(
        "lockfileVersion: '9.0'\n\npackages:\n\n  left-pad@1.3.0:\n    resolution: {tarball: http://x}\n",
        encoding="utf-8",
    )

    assert any("no integrity hash" in error for error in check_pins.check(root))


def test_a_version_that_does_not_match_the_pin_is_drift() -> None:
    pins = {"fastapi": check_pins.Pin("fastapi", "0.162.7", ("a" * 64,), None)}
    errors: list[str] = []

    check_pins.drift("requirements.lock", pins, "fastapi==0.162.8\n", errors)

    assert errors == ["drift: fastapi==0.162.8 installed, pin is 0.162.7"]


def test_a_missing_pin_and_an_extra_distribution_are_drift() -> None:
    pins = {"fastapi": check_pins.Pin("fastapi", "0.162.7", ("a" * 64,), None)}
    errors: list[str] = []

    check_pins.drift("requirements.lock", pins, "httpx==0.28.1\n", errors)

    assert errors == [
        "drift: fastapi==0.162.7 is pinned but not installed",
        "drift: httpx==0.28.1 is installed but not pinned",
    ]


def test_a_marker_that_does_not_apply_here_is_not_drift() -> None:
    pins = {
        "fastapi": check_pins.Pin("fastapi", "0.162.7", ("a" * 64,), None),
        "colorama": check_pins.Pin("colorama", "0.4.6", ("a" * 64,), 'sys_platform == "win32"'),
    }
    errors: list[str] = []

    check_pins.drift("requirements.lock", pins, "fastapi==0.162.7\n", errors)

    assert errors == []


def test_an_editable_install_does_not_count_as_a_drifted_pin() -> None:
    pins = {"fastapi": check_pins.Pin("fastapi", "0.162.7", ("a" * 64,), None)}
    errors: list[str] = []

    check_pins.drift(
        "requirements.lock",
        pins,
        "fastapi==0.162.7\n-e git+https://example.invalid/ezchain.git#egg=ezchain_core\n",
        errors,
    )

    assert errors == []


def test_the_audit_fails_on_a_known_vulnerable_pin() -> None:
    report = {
        "dependencies": [
            {
                "name": "jinja2",
                "version": "2.11.2",
                "vulns": [
                    {
                        "id": "PYSEC-2021-66",
                        "aliases": ["CVE-2020-28493"],
                        "fix_versions": ["2.11.3"],
                    }
                ],
            }
        ]
    }

    errors = audit_pins.python_verdict(report, {})

    assert errors == [
        "vulnerable pin: jinja2==2.11.2 CVE-2020-28493, PYSEC-2021-66 (fixed in 2.11.3)"
    ]


def test_the_audit_passes_a_clean_report() -> None:
    assert (
        audit_pins.python_verdict(
            {"dependencies": [{"name": "fastapi", "version": "0.162.7", "vulns": []}]}, {}
        )
        == []
    )


def test_an_allowlisted_advisory_needs_a_reason(tmp_path: Path) -> None:
    allowlist = tmp_path / "allowlist.txt"
    allowlist.write_text("# comment\nPYSEC-2021-66\n", encoding="utf-8")

    with pytest.raises(audit_pins.AuditUnavailable, match="needs an id and a reason"):
        audit_pins.load_allowlist(allowlist)


def test_an_allowlisted_advisory_with_a_reason_does_not_fail_the_audit(tmp_path: Path) -> None:
    allowlist = tmp_path / "allowlist.txt"
    allowlist.write_text(
        "PYSEC-2021-66 not reachable: no templates are rendered\n", encoding="utf-8"
    )
    report = {
        "dependencies": [
            {
                "name": "jinja2",
                "version": "2.11.2",
                "vulns": [{"id": "PYSEC-2021-66", "aliases": ["CVE-2020-28493"]}],
            }
        ]
    }

    allowed = audit_pins.load_allowlist(allowlist)

    assert audit_pins.python_verdict(report, allowed) == []
    assert allowed == {"PYSEC-2021-66": "not reachable: no templates are rendered"}


def test_a_node_advisory_fails_the_audit_and_is_named() -> None:
    report = {
        "advisories": {
            "1117015": {
                "module": "postcss",
                "severity": "high",
                "vulnerable_versions": "<8.5.10",
                "title": "PostCSS has XSS via unescaped </style>",
                "references": "- https://github.com/advisories/GHSA-qx2v-qp2m-jg93",
            }
        }
    }

    errors = audit_pins.node_verdict(report, {})

    assert len(errors) == 1
    assert errors[0].startswith("vulnerable pin: postcss <8.5.10 (high)")
    assert "GHSA-qx2v-qp2m-jg93" in errors[0]


def test_a_saved_report_is_judged_with_the_same_verdict(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "dependencies": [
                    {"name": "jinja2", "version": "2.11.2", "vulns": [{"id": "PYSEC-2021-66"}]}
                ]
            }
        ),
        encoding="utf-8",
    )

    assert audit_pins.main(["--report", str(report)]) == 1


def test_an_audit_that_cannot_run_is_not_a_pass(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-python"

    assert audit_pins.main(["--python", "--python-executable", str(missing)]) == 2


@pytest.mark.skipif(shutil.which("pip-audit") is None, reason="pip-audit is not installed here")
def test_the_real_python_locks_have_no_unallowlisted_advisory() -> None:
    """Needs a network for the advisory service; skipped without one, never silently green."""
    result = audit_pins.main(["--python"])

    assert result in (0, 2), "a run that reached the service must be a clean pass"


SIGNER_OR_CUSTODY = (
    "web3",
    "eth-account",
    "eth_account",
    "eth-keys",
    "coincurve",
    "ecdsa",
    "solana",
    "solders",
    "anchorpy",
    "bitcoinlib",
    "python-bitcoinlib",
    "py-evm",
    "viem",
    "ethers",
    "@solana/web3.js",
    "@solana/kit",
    "@noble/secp256k1",
    "@noble/curves",
    "@scure/bip39",
    "bip39",
    "tweetnacl",
    "bitcoinjs-lib",
)


def test_the_pinned_set_carries_no_signer_or_custody_library() -> None:
    """A package that can sign or hold funds needs an ADR before it is pinned.

    The executor takes its signer from the caller, so the platform has no vendor signer
    SDK. If one appears in a lock, docs/DEPENDENCIES.md must record the ADR that
    accepted it in the same change; this test is the tripwire, not the approval.
    """
    pins = {
        pin
        for lock in ("requirements.lock", "requirements-runtime.lock")
        for pin in check_pins.parse_lock(ROOT / lock)
    }
    node = (ROOT / "pnpm-lock.yaml").read_text(encoding="utf-8")
    documentation = (ROOT / "docs" / "DEPENDENCIES.md").read_text(encoding="utf-8")

    found = sorted(package for package in SIGNER_OR_CUSTODY if package in pins)
    found += sorted(package for package in SIGNER_OR_CUSTODY if f"  {package}@" in node)

    assert found == [], (
        f"signer/custody packages {found} are pinned without a recorded ADR; "
        "docs/DEPENDENCIES.md §4 is where the decision is written"
    )
    assert "ADR" in documentation
