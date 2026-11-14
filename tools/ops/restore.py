"""Restore a recorded backup into a fresh database, then prove it reproduces the ledger.

A drill is not "the restore command exited 0". It is:

1. a database that did not exist before is created;
2. the dump is loaded into it;
3. every ledger table matches the manifest row-for-row (digest, not just a count);
4. the API's own reconciliation report returns ``ok: true`` with no failing check;
5. the restored schema is at or above the head the backup was taken at.

A short restore — a dump that loaded only some rows, or a schema the application no
longer accepts — fails at step 3 or 4 with the table and the difference named, which is
what the runbook's recovery section tells the operator to do with.

The target must be a disposable database: its name has to contain ``test``, ``scratch``,
``rehearsal`` or ``_ci``. It is dropped and recreated, so a typo cannot overwrite a live
database.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .fingerprint import (
    LEDGER_TABLES,
    compare_tables,
    fingerprint_digest,
    ledger_counts,
    reconcile,
    table_digests,
)
from .pg import OpsError, PgTarget, require_binary, require_disposable


@dataclass(frozen=True)
class LoadedManifest:
    path: Path
    document: dict[str, Any]

    @property
    def sha256(self) -> str:
        return str(self.document.get("dump", {}).get("sha256", ""))

    @property
    def dump_path(self) -> str:
        return str(self.document.get("dump", {}).get("path", ""))

    @property
    def tables(self) -> dict[str, Any]:
        return dict(self.document.get("tables", {}))

    @property
    def alembic_version(self) -> str | None:
        value = self.document.get("source", {}).get("alembic_version")
        return None if value is None else str(value)


@dataclass
class RestoreResult:
    target: str
    created: bool
    loaded: str
    counts: dict[str, int]
    reconciliation: dict[str, Any]
    differences: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.differences and bool(self.reconciliation.get("ok"))

    def summary(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "created": self.created,
            "loaded": self.loaded,
            "counts": self.counts,
            "reconciliation": self.reconciliation,
            "differences": self.differences,
            "ok": self.ok,
        }


def load_manifest(path: Path) -> LoadedManifest:
    if not path.is_file():
        raise OpsError(f"manifest not found: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OpsError(f"manifest is not valid JSON: {path}: {exc}") from exc
    if not isinstance(document, dict) or "tables" not in document or "dump" not in document:
        raise OpsError(f"manifest is not a backup manifest: {path}")
    return LoadedManifest(path=path, document=document)


def verify_dump_digest(manifest: LoadedManifest, dump: Path) -> None:
    """Refuse a dump that changed since the manifest recorded it."""
    if not dump.is_file():
        raise OpsError(f"dump not found: {dump}")
    digest = hashlib.sha256()
    with dump.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != manifest.sha256:
        raise OpsError(
            f"dump {dump} does not match its manifest: sha256 {actual} != {manifest.sha256}"
        )


def _recreate_database(target: PgTarget) -> None:
    """Drop and create the target database through the maintenance connection."""
    require_disposable(target)
    admin = target.with_database("postgres")
    conn = admin.connect(autocommit=True)
    try:
        # A quote-escaped identifier cannot be a bind parameter; the name was checked to
        # contain no quote character by ``_identifier``.
        name = _identifier(target.dbname)
        conn.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        conn.execute(f"CREATE DATABASE {name}")
    finally:
        conn.close()


def _identifier(name: str) -> str:
    if '"' in name or "\x00" in name:
        raise OpsError(f"refusing an unsafe database name: {name!r}")
    return f'"{name}"'


def _pg_restore(pg_restore_bin: str, target: PgTarget, dump: Path) -> None:
    result = subprocess.run(
        [pg_restore_bin, *target.libpq_args(), "--no-owner", "--exit-on-error", str(dump)],
        capture_output=True,
        text=True,
        env=target.child_env(),
        check=False,
    )
    if result.returncode != 0:
        raise OpsError(
            f"pg_restore failed for {target.redact()} (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )


def _database_url(target: PgTarget) -> str:
    from sqlalchemy.engine import URL

    return URL.create(
        "postgresql+psycopg",
        username=target.user,
        password=target.password,
        host=target.host,
        port=target.port,
        database=target.dbname,
    ).render_as_string(hide_password=False)


def _schema_ok(restored_version: str | None, backed_up_version: str | None) -> list[str]:
    """A restore may equal or improve on the dumped schema, never fall behind it."""
    if backed_up_version is None:
        return []
    if restored_version is None:
        return [f"schema: restored database has no alembic_version, backup was {backed_up_version}"]
    if restored_version != backed_up_version:
        return [
            f"schema: restored database is at {restored_version}, backup was {backed_up_version}; "
            "run `ezchain-api migrate` against the restored database and re-verify"
        ]
    return []


def restore(
    database_url: str,
    manifest_path: Path,
    *,
    dump: Path | None = None,
    pg_restore_bin: str | None = None,
) -> RestoreResult:
    """Load a backup into a fresh database and verify the ledger it reproduces."""
    manifest = load_manifest(manifest_path)
    dump_path = dump if dump is not None else manifest_path.parent / manifest.dump_path
    verify_dump_digest(manifest, dump_path)

    target = PgTarget.parse(database_url)
    require_disposable(target)
    pg_restore = require_binary("pg_restore", pg_restore_bin)

    _recreate_database(target)
    _pg_restore(pg_restore, target, dump_path)

    restored = table_digests(target)
    expected_tables = manifest.tables
    actual_tables = dict(restored["tables"])

    differences = compare_tables(expected_tables, actual_tables, only=LEDGER_TABLES)
    differences += _schema_ok(restored["alembic_version"], manifest.alembic_version)

    report = reconcile(_database_url(target))
    reconciliation = {
        "ok": report.ok,
        "detail": report.detail,
        "totals": report.totals,
    }
    if not report.ok:
        differences.append(f"reconciliation: {report.detail}")

    return RestoreResult(
        target=target.redact(),
        created=True,
        loaded=str(dump_path),
        counts=ledger_counts(actual_tables),
        reconciliation=reconciliation,
        differences=differences,
    )


def drop_target(database_url: str) -> None:
    """Remove a rehearsal database. The name must still look disposable."""
    target = PgTarget.parse(database_url)
    require_disposable(target)
    admin = target.with_database("postgres")
    conn = admin.connect(autocommit=True)
    try:
        conn.execute(f"DROP DATABASE IF EXISTS {_identifier(target.dbname)} WITH (FORCE)")
    finally:
        conn.close()


def verify_fingerprint(database_url: str, manifest_path: Path) -> dict[str, Any]:
    """Compare an existing database to a manifest without touching it."""
    manifest = load_manifest(manifest_path)
    target = PgTarget.parse(database_url)
    actual = table_digests(target)
    differences = compare_tables(manifest.tables, dict(actual["tables"]), only=LEDGER_TABLES)
    differences += _schema_ok(actual["alembic_version"], manifest.alembic_version)
    return {
        "database": target.redact(),
        "differences": differences,
        "counts": ledger_counts(dict(actual["tables"])),
        "alembic_version": actual["alembic_version"],
        "fingerprint": fingerprint_digest(dict(actual["tables"])),
        "matches": not differences,
    }


__all__ = [
    "LoadedManifest",
    "RestoreResult",
    "drop_target",
    "load_manifest",
    "restore",
    "verify_dump_digest",
    "verify_fingerprint",
]
