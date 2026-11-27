"""First-run readiness: read-only facts, fail-closed exit codes, no secrets in output."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ezchain_api import cli
from ezchain_api.config import Settings

# Obviously invalid test constants. Never a real credential.
TEST_TOKEN = "TEST_TOKEN_DO_NOT_USE_9f3a"
TEST_PEPPER = "TEST_PEPPER_DO_NOT_USE_7c1d"
TEST_DB_PASSWORD = "TEST_DB_PASSWORD_DO_NOT_USE_4b2e"
HEAD = "0031_pair_address_width"
BEHIND = "0002_genesis_index"


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "data_mode": "fixture",
        "database_url": (f"postgresql+psycopg://ezchain:{TEST_DB_PASSWORD}@127.0.0.1:5432/ezchain"),
        "auto_create_schema": False,
        "api_token": TEST_TOKEN,
        "api_key_pepper": TEST_PEPPER,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def verified_backup(tmp_path: Path, *, hours_old: float = 1.0, label: str = "ezchain") -> Path:
    payload = b"a dump that is not a real database"
    dump = tmp_path / f"{label}.dump"
    dump.write_bytes(payload)
    document = {
        "tool": "tools.ops.backup",
        "format_version": 1,
        "label": label,
        "created_at": (datetime.now(UTC) - timedelta(hours=hours_old)).isoformat(),
        "dump": {"path": dump.name, "sha256": hashlib.sha256(payload).hexdigest()},
        "tables": {"paper_orders": {"rows": 1, "digest": "unit-test"}},
    }
    (tmp_path / f"{label}.manifest.json").write_text(json.dumps(document))
    return tmp_path


def report(
    tmp_path: Path,
    *,
    config: Settings | None = None,
    bind: str = "127.0.0.1",
    applied: str | None = HEAD,
    head: str | None = HEAD,
    max_age_hours: float = 24,
) -> cli.Readiness:
    return cli.build_readiness(
        config or settings(),
        bind=bind,
        backup_dir=tmp_path,
        max_backup_age_hours=max_age_hours,
        applied_revision=lambda: applied,
        head_revision=lambda: head,
        now=datetime.now(UTC),
    )


def test_all_checks_pass_when_backed_up_at_head(tmp_path: Path) -> None:
    result = report(verified_backup(tmp_path))

    assert result.ok
    assert result.problems == ()
    assert result.backup.status == "verified"
    assert "READY" in "\n".join(result.lines())


def test_non_loopback_listener_without_token_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = settings(api_token=None, environment="dev")
    code = cli.run_readiness(
        config,
        bind="0.0.0.0",
        backup_dir=verified_backup(tmp_path),
        max_backup_age_hours=24,
        applied_revision=lambda: HEAD,
        head_revision=lambda: HEAD,
    )

    assert code != 0
    assert "EZCHAIN_API_TOKEN" in capsys.readouterr().out


def test_loopback_without_token_is_reported_but_not_a_failure(tmp_path: Path) -> None:
    result = report(verified_backup(tmp_path), config=settings(api_token=None))

    assert result.ok
    assert not result.token_set
    assert "unset" in "\n".join(result.lines())


def test_schema_behind_head_exits_nonzero(tmp_path: Path) -> None:
    result = report(verified_backup(tmp_path), applied=BEHIND, head=HEAD)

    assert not result.ok
    assert any("behind" in problem for problem in result.problems)
    assert "behind" in "\n".join(result.lines())


def test_missing_and_unreadable_revisions_fail(tmp_path: Path) -> None:
    missing = report(verified_backup(tmp_path), applied=None, head=HEAD)
    unreadable = report(verified_backup(tmp_path), applied=HEAD, head=None)

    assert not missing.ok
    assert not unreadable.ok


def test_no_backup_exits_nonzero(tmp_path: Path) -> None:
    result = report(tmp_path)

    assert not result.ok
    assert result.backup.status == "none"
    assert any("no verified backup" in problem for problem in result.problems)


def test_stale_backup_exits_nonzero(tmp_path: Path) -> None:
    result = report(verified_backup(tmp_path, hours_old=100), max_age_hours=24)

    assert not result.ok
    assert result.backup.status == "stale"
    assert any("older than" in problem for problem in result.problems)


def test_tampered_dump_is_not_a_verified_backup(tmp_path: Path) -> None:
    directory = verified_backup(tmp_path)
    (directory / "ezchain.dump").write_bytes(b"changed after the manifest was written")

    result = report(directory)

    assert not result.ok
    assert result.backup.status == "none" or result.backup.status == "unverified"


def test_a_bad_manifest_is_not_a_verified_backup(tmp_path: Path) -> None:
    (tmp_path / "ezchain.manifest.json").write_text('{"tool": "not-a-backup"}')

    result = report(tmp_path)

    assert not result.ok
    assert result.backup.status in {"none", "unverified"}


def test_a_non_hex_digest_cannot_break_the_report(tmp_path: Path) -> None:
    (tmp_path / "ezchain.manifest.json").write_text(
        json.dumps(
            {
                "tool": "tools.ops.backup",
                "format_version": 1,
                "created_at": datetime.now(UTC).isoformat(),
                "dump": {"path": "ezchain.dump", "sha256": "\u00e9" * 64},
                "tables": {"paper_orders": {"rows": 1}},
            }
        )
    )

    result = report(tmp_path)

    assert not result.ok
    assert result.backup.status == "unverified"


def test_read_authentication_mode_is_reported(tmp_path: Path) -> None:
    directory = verified_backup(tmp_path)
    required = report(directory, config=settings(require_auth_for_reads=True))
    open_reads = report(directory, config=settings(require_auth_for_reads=False))

    assert required.reads_require_auth
    assert "required" in "\n".join(required.lines())
    assert not open_reads.reads_require_auth
    assert "open" in "\n".join(open_reads.lines())


def test_output_contains_no_credential_material(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.run_readiness(
        settings(),
        bind="0.0.0.0",
        backup_dir=verified_backup(tmp_path),
        max_backup_age_hours=24,
        applied_revision=lambda: BEHIND,
        head_revision=lambda: HEAD,
    )
    output = capsys.readouterr().out

    for secret in (TEST_TOKEN, TEST_PEPPER, TEST_DB_PASSWORD, "Bearer"):
        assert secret not in output


def test_cli_dispatches_readiness_and_returns_its_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: settings())
    monkeypatch.setattr(cli, "_probe_applied_revision", lambda _url: HEAD)
    monkeypatch.setattr(cli, "_probe_head_revision", lambda: HEAD)

    code = cli.main(
        [
            "readiness",
            "--bind",
            "127.0.0.1",
            "--backup-dir",
            str(verified_backup(tmp_path)),
        ]
    )

    assert code == 0
    assert "result: READY" in capsys.readouterr().out
