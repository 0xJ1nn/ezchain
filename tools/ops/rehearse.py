"""End-to-end restore rehearsal with a measured recovery time.

The rehearsal does what an incident does, not what a happy path does:

1. create a disposable source database and migrate it to head;
2. fill it through the real paper order path (``tools.ops.seed``);
3. take a scripted backup and record its fingerprint;
4. **destroy the source database** — this is the corruption the drill must survive;
5. restore the dump into a *different* fresh database and verify it row-for-row and with
   the API's reconciliation report.

The recovery time is the wall-clock span of step 5 only: from "the database is gone" to
"a verified copy is serving". Step 4 is instantaneous by construction, so the number
answers the only question an operator has after a loss.

Every measurement in the evidence pack comes from this function running against a real
Postgres, and nothing is inferred.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .backup import BackupResult, backup
from .migrate import migrate
from .pg import OpsError, PgTarget, require_disposable
from .restore import RestoreResult, restore
from .seed import SeedResult, seed


@dataclass
class RehearsalResult:
    source: str
    target: str
    backup: BackupResult
    seed: SeedResult
    restore: RestoreResult
    timings: dict[str, float]
    started_at: str
    finished_at: str
    dropped_source: bool
    artefacts: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.restore.ok

    def document(self) -> dict[str, Any]:
        return {
            "tool": "tools.ops.rehearse",
            "format_version": 1,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "source": self.source,
            "target": self.target,
            "dropped_source": self.dropped_source,
            "seed": {
                "orders": self.seed.orders,
                "fills": self.seed.fills,
                "replayed": self.seed.replayed,
            },
            "backup": self.backup.summary(),
            "timings": self.timings,
            "restore": self.restore.summary(),
            "artefacts": self.artefacts,
            "ok": self.ok,
        }


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _create_database(target: PgTarget) -> None:
    require_disposable(target)
    admin = target.with_database("postgres")
    conn = admin.connect(autocommit=True)
    try:
        conn.execute(f'DROP DATABASE IF EXISTS "{target.dbname}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{target.dbname}"')
    finally:
        conn.close()


def _drop_database(target: PgTarget) -> None:
    require_disposable(target)
    admin = target.with_database("postgres")
    conn = admin.connect(autocommit=True)
    try:
        conn.execute(f'DROP DATABASE IF EXISTS "{target.dbname}" WITH (FORCE)')
    finally:
        conn.close()


def rehearse(
    admin_url: str,
    dest: Path,
    *,
    name_prefix: str = "ezchain_rehearsal",
    drop_source: bool = True,
    keep_databases: bool = False,
    pg_dump_bin: str | None = None,
    pg_restore_bin: str | None = None,
) -> RehearsalResult:
    """Run the whole drill against one Postgres server and return its measurements."""
    base = PgTarget.parse(admin_url)
    strain = _stamp()
    source_name = f"{name_prefix}_source_{strain}_test"
    target_name = f"{name_prefix}_restore_{strain}_test"
    source = base.with_database(source_name)
    target = base.with_database(target_name)
    started = datetime.now(UTC)

    timings: dict[str, float] = {}
    try:
        phase = time.monotonic()
        _create_database(source)
        migrate(source_name, base)
        timings["provision_seconds"] = time.monotonic() - phase

        phase = time.monotonic()
        seeded = seed(_database_url(source))
        timings["seed_seconds"] = time.monotonic() - phase

        phase = time.monotonic()
        result = backup(
            _database_url(source),
            dest,
            label=f"{name_prefix}-{strain}",
            force=True,
            pg_dump_bin=pg_dump_bin,
        )
        timings["backup_seconds"] = time.monotonic() - phase

        if result.document.get("counts", {}).get("ledger_entries", 0) <= 2:
            raise OpsError(
                "the backup holds only the genesis journal; the rehearsal needs real rows"
            )

        if drop_source:
            _drop_database(source)

        phase = time.monotonic()
        restored = restore(
            _database_url(target),
            result.manifest,
            dump=result.dump,
            pg_restore_bin=pg_restore_bin,
        )
        timings["recovery_seconds"] = time.monotonic() - phase
        timings["recovery_seconds_rounded"] = round(timings["recovery_seconds"], 3)
    finally:
        if not keep_databases:
            _drop_database(source)
            _drop_database(target)

    finished = datetime.now(UTC)
    artefacts = {"dump": str(result.dump), "manifest": str(result.manifest)}
    return RehearsalResult(
        source=source.redact(),
        target=target.redact(),
        backup=result,
        seed=seeded,
        restore=restored,
        timings=timings,
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        dropped_source=drop_source,
        artefacts=artefacts,
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


def write_report(result: RehearsalResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result.document(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


__all__ = ["RehearsalResult", "rehearse", "write_report"]
