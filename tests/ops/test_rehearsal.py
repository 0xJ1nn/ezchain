"""The rehearsal against a real Postgres: a verified restore, and a short one that fails.

These skip, never fake, when ``EZCHAIN_TEST_DATABASE_URL`` is unset — the same rule the
migration gate follows. The failure path is the acceptance criterion that matters most:
a restore that loses rows must be *detected*, not merely attempted.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy.engine import make_url
from tools.ops.backup import backup
from tools.ops.fingerprint import table_digests
from tools.ops.migrate import migrate
from tools.ops.pg import PgTarget
from tools.ops.rehearse import rehearse
from tools.ops.restore import drop_target, restore, verify_fingerprint
from tools.ops.seed import seed

pytestmark = pytest.mark.skipif(
    not os.environ.get("EZCHAIN_TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="EZCHAIN_TEST_DATABASE_URL is not a Postgres test database",
)


def _base_url() -> str:
    return os.environ["EZCHAIN_TEST_DATABASE_URL"]


def _named(name: str) -> str:
    parsed = make_url(_base_url())
    return parsed.set(database=name).render_as_string(hide_password=False)


@pytest.fixture(scope="module")
def tools_available() -> None:
    for binary in ("pg_dump", "pg_restore"):
        if shutil.which(binary) is None:
            pytest.skip(f"{binary} is not on PATH")


@pytest.fixture
def databases(tools_available: None) -> Iterator[tuple[str, str]]:
    """Two disposable database names on the test server, dropped afterwards."""
    stamp = uuid4().hex[:8]
    source_name = f"ezchain_ops_source_{stamp}_test"
    target_name = f"ezchain_ops_restore_{stamp}_test"
    yield source_name, target_name
    for name in (source_name, target_name):
        drop_target(_named(name))


def _create(name: str) -> str:
    base = PgTarget.parse(_named(name))
    admin = base.with_database("postgres")
    with admin.connect(autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{name}"')
    migrate(name, base)
    return _named(name)


def _row_count(url: str, table: str) -> int:
    with psycopg.connect(url.replace("postgresql+psycopg", "postgresql"), autocommit=True) as conn:
        return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def test_a_fresh_restore_reproduces_the_ledger_row_for_row(
    databases: tuple[str, str], tmp_path: Path
) -> None:
    """The acceptance criterion: same fills, same positions, same reconciliation result."""
    source_name, target_name = databases
    source = _create(source_name)
    seeded = seed(source)
    assert seeded.orders >= 7
    assert seeded.fills >= 7
    assert seeded.replayed == 1, "the scenario replays one idempotency key"

    taken = backup(source, tmp_path, label="drill", force=True)
    assert taken.bytes > 0
    assert taken.document["counts"]["ledger_entries"] > 2

    frozen = table_digests(PgTarget.parse(source))

    result = restore(_named(target_name), taken.manifest, dump=taken.dump)

    assert result.ok, result.differences
    assert result.reconciliation["ok"] is True
    assert result.counts["ledger_entries"] == frozen["tables"]["ledger_entries"]["rows"]
    assert result.counts["paper_fills"] == frozen["tables"]["paper_fills"]["rows"]

    restored = table_digests(PgTarget.parse(_named(target_name)))
    assert restored["tables"] == frozen["tables"]
    assert restored["alembic_version"] == frozen["alembic_version"]


def test_a_restore_that_is_short_is_reported_as_a_failed_drill(
    databases: tuple[str, str], tmp_path: Path
) -> None:
    """Drop a fill from the restored copy: the digest must catch it, count and all."""
    source_name, target_name = databases
    source = _create(source_name)
    seed(source)
    taken = backup(source, tmp_path, label="drill", force=True)

    dropped = restore(_named(target_name), taken.manifest, dump=taken.dump)
    assert dropped.ok, dropped.differences

    # Corrupt the restore exactly as a truncated load would: same schema, fewer rows.
    with psycopg.connect(
        _named(target_name).replace("postgresql+psycopg", "postgresql"), autocommit=True
    ) as conn:
        conn.execute(
            "DELETE FROM paper_fills WHERE id = (SELECT id FROM paper_fills ORDER BY id LIMIT 1)"
        )

    check = verify_fingerprint(_named(target_name), taken.manifest)

    assert check["matches"] is False
    assert any(line.startswith("paper_fills:") for line in check["differences"]), check[
        "differences"
    ]


def test_a_dump_that_changed_after_the_manifest_is_refused(
    databases: tuple[str, str], tmp_path: Path
) -> None:
    source_name, target_name = databases
    source = _create(source_name)
    seed(source)
    taken = backup(source, tmp_path, label="drill", force=True)

    taken.dump.write_bytes(taken.dump.read_bytes() + b"corruption")

    from tools.ops.pg import OpsError

    with pytest.raises(OpsError, match="does not match its manifest"):
        restore(_named(target_name), taken.manifest, dump=taken.dump)


def test_the_rehearsal_measures_a_recovery_after_destroying_the_source(
    databases: tuple[str, str], tmp_path: Path
) -> None:
    """End to end: build, back up, drop the source database, restore, time it."""
    result = rehearse(
        _named("postgres"),
        tmp_path,
        name_prefix="ezchain_ops_rehearse",
        keep_databases=False,
    )

    assert result.dropped_source is True
    assert result.ok, result.restore.differences
    assert result.restore.reconciliation["ok"] is True
    assert result.timings["recovery_seconds"] > 0
    assert result.backup.document["counts"]["ledger_entries"] > 2
    # The rehearsal databases are named from a timestamp, so clean them up here.
    for url in (result.source, result.target):
        drop_target(url.replace("postgresql://", "postgresql+psycopg://", 1))

    manifest = json.loads(Path(result.backup.manifest).read_text(encoding="utf-8"))
    assert manifest["counts"]["paper_fills"] == result.seed.fills
    assert manifest["tables"]["ledger_entries"]["rows"] == result.restore.counts["ledger_entries"]


def test_verify_reports_a_database_without_a_schema_instead_of_raising(
    databases: tuple[str, str], tmp_path: Path
) -> None:
    """An empty database is a reportable short restore, not a traceback."""
    source_name, target_name = databases
    source = _create(source_name)
    seed(source)
    taken = backup(source, tmp_path, label="drill", force=True)

    base = PgTarget.parse(_named(target_name))
    with base.with_database("postgres").connect(autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{target_name}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{target_name}"')

    check = verify_fingerprint(_named(target_name), taken.manifest)

    assert check["matches"] is False
    assert check["alembic_version"] is None
    assert any("missing in restore" in line for line in check["differences"]), check["differences"]


def test_the_backup_records_a_source_it_never_writes(
    databases: tuple[str, str], tmp_path: Path
) -> None:
    """A backup run twice on an unchanged source reuses a verified dump."""
    source_name, _ = databases
    source = _create(source_name)
    seed(source)

    first = backup(source, tmp_path, label="idem", force=True)
    before = _row_count(source, "ledger_entries")
    second = backup(source, tmp_path, label="idem")

    assert second.reused is True
    assert second.sha256 == first.sha256
    assert second.dump == first.dump
    assert _row_count(source, "ledger_entries") == before
    assert len(list(tmp_path.glob("*.dump"))) == 1


def test_a_changed_source_is_never_answered_with_a_stale_dump(
    databases: tuple[str, str], tmp_path: Path
) -> None:
    """The dangerous case: rows moved on, so the old dump must not be reused."""
    source_name, _ = databases
    source = _create(source_name)
    seed(source)
    first = backup(source, tmp_path, label="stale", force=True)

    with psycopg.connect(
        source.replace("postgresql+psycopg", "postgresql"), autocommit=True
    ) as conn:
        conn.execute("DELETE FROM ledger_entries WHERE seq = (SELECT max(seq) FROM ledger_entries)")

    second = backup(source, tmp_path, label="stale")

    assert second.reused is False
    assert second.sha256 != first.sha256
    before = first.document["counts"]["ledger_entries"]
    assert second.document["counts"]["ledger_entries"] == before - 1
