"""Scripted, idempotent Postgres backup with a ledger fingerprint.

A dump on its own proves nothing: ``pg_dump`` exits 0 for a database whose rows are
already wrong. Every backup therefore records two things in one manifest — the dump's
SHA-256, and a digest of every table read from the *same exported snapshot* that produced
the dump. ``tools.ops.restore`` compares a restored database against that manifest, so a
backup is only accepted once it has been rebuilt.

Running ``backup`` twice with the same ``--label`` is idempotent: the second run verifies
the existing dump against its manifest and reuses it instead of writing a second copy.
``--force`` takes a new dump.

The source database is only ever read. ``pg_dump`` takes an ``ACCESS SHARE`` lock, and the
fingerprint runs inside the transaction that exports the snapshot.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .fingerprint import (
    alembic_version,
    compare_tables,
    fingerprint_digest,
    ledger_counts,
    table_digests,
    table_digests_on,
)
from .pg import OpsError, PgTarget, require_binary


@dataclass(frozen=True)
class BackupResult:
    dump: Path
    manifest: Path
    sha256: str
    bytes: int
    reused: bool
    document: dict[str, Any]

    def summary(self) -> dict[str, Any]:
        return {
            "dump": str(self.dump),
            "manifest": str(self.manifest),
            "sha256": self.sha256,
            "bytes": self.bytes,
            "reused": self.reused,
            "source": self.document["source"]["database"],
            "alembic_version": self.document["source"]["alembic_version"],
            "counts": self.document["counts"],
            "fingerprint": self.document["fingerprint"],
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pg_dump_version(pg_dump: str) -> str:
    result = subprocess.run([pg_dump, "--version"], capture_output=True, text=True, check=False)
    return result.stdout.strip() or "unknown"


def _load_manifest(manifest: Path) -> dict[str, Any] | None:
    if not manifest.is_file():
        return None
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return document if isinstance(document, dict) else None


def _reuse_existing(source: PgTarget, dump_path: Path, manifest_path: Path) -> BackupResult | None:
    """Reuse a verified dump only when the source still holds exactly what it recorded.

    Reusing on the dump's hash alone would be wrong in the one case that matters: the
    source moved on since the dump, so the "existing backup" is stale and silently
    returning it makes the tool claim coverage it does not have. Both sides must agree.
    """
    existing = _load_manifest(manifest_path)
    if existing is None or not dump_path.is_file():
        return None
    recorded = existing.get("dump", {}).get("sha256")
    actual = sha256_file(dump_path)
    if recorded != actual:
        return None
    recorded_tables = existing.get("tables")
    if not isinstance(recorded_tables, dict) or not recorded_tables:
        return None
    try:
        current = table_digests(source)["tables"]
    except Exception:  # noqa: BLE001 - an unreadable source takes a fresh dump instead
        return None
    if compare_tables(recorded_tables, current) != []:
        return None
    return BackupResult(
        dump=dump_path,
        manifest=manifest_path,
        sha256=actual,
        bytes=dump_path.stat().st_size,
        reused=True,
        document=existing,
    )


def backup(
    database_url: str,
    dest: Path,
    *,
    label: str | None = None,
    force: bool = False,
    pg_dump_bin: str | None = None,
) -> BackupResult:
    """Dump the database and record the fingerprint of the ledger it contained."""
    source = PgTarget.parse(database_url)
    dest.mkdir(parents=True, exist_ok=True)
    name = label or source.dbname
    if not name or "/" in name or name in {".", ".."}:
        raise OpsError(f"invalid backup label: {name!r}")
    dump_path = dest / f"{name}.dump"
    manifest_path = dest / f"{name}.manifest.json"

    if not force:
        reused = _reuse_existing(source, dump_path, manifest_path)
        if reused is not None:
            return reused

    pg_dump = require_binary("pg_dump", pg_dump_bin)
    partial = dest / f".{name}.dump.partial"
    partial.unlink(missing_ok=True)

    conn = source.connect()
    try:
        # A timestamptz renders in the session's zone, and the composite row text is what
        # the digest hashes. Pin UTC here as ``fingerprint._connect`` does, or the source
        # and the restore would disagree about the same row.
        conn.execute("SET TIME ZONE 'UTC'")
        # ``SET TRANSACTION ISOLATION LEVEL`` is only legal before the first query in a
        # transaction, so close the implicit one the SET opened.
        conn.commit()
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            snapshot = conn.execute("SELECT pg_export_snapshot()").fetchone()[0]
            result = subprocess.run(
                [
                    pg_dump,
                    *source.libpq_args(),
                    "--snapshot",
                    snapshot,
                    "--no-owner",
                    "-Fc",
                    "-f",
                    str(partial),
                ],
                capture_output=True,
                text=True,
                env=source.child_env(),
                check=False,
            )
            if result.returncode != 0:
                partial.unlink(missing_ok=True)
                raise OpsError(f"pg_dump failed for {source.redact()}: {result.stderr.strip()}")
            # One snapshot for both the dump and the fingerprint.
            digests = table_digests_on(conn)
            version = alembic_version(conn)
    finally:
        conn.close()

    if not partial.is_file() or partial.stat().st_size == 0:
        partial.unlink(missing_ok=True)
        raise OpsError(f"pg_dump produced no data for {source.redact()}")

    digest = sha256_file(partial)
    partial.replace(dump_path)

    document: dict[str, Any] = {
        "tool": "tools.ops.backup",
        "format_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "label": name,
        "dump": {
            "path": dump_path.name,
            "sha256": digest,
            "bytes": dump_path.stat().st_size,
            "format": "custom",
            "pg_dump": _pg_dump_version(pg_dump),
        },
        "source": {
            "database": source.redact(),
            "alembic_version": version,
        },
        "counts": ledger_counts(digests),
        "fingerprint": fingerprint_digest(digests),
        "tables": digests,
    }
    manifest_path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return BackupResult(
        dump=dump_path,
        manifest=manifest_path,
        sha256=digest,
        bytes=dump_path.stat().st_size,
        reused=False,
        document=document,
    )


__all__ = ["BackupResult", "backup", "sha256_file"]
