"""Canonical digests of a database, and the checks a restore must pass.

A backup is only evidence when the restored database can be compared to the one that was
dumped. Every table is read in a fixed order, each row is rendered as its full composite
text (all columns, including columns added by a later migration), and the ordered text is
reduced to one MD5 per table. Both sides of a comparison read the same schema through the
same query, so an equal digest means equal rows, not merely equal counts.

The ledger is the accountant. ``reconcile`` runs the API's own reconciliation report
against whatever database it is handed, so a restored copy is judged by the same rule the
running system reports to its dashboard.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ezchain_api.services.reconciliation import reconciliation

from .pg import PgTarget

# (table, ORDER BY expression). The order is part of the evidence: a digest over
# unordered rows would call two different journals equal.
TABLES: tuple[tuple[str, str], ...] = (
    ("ledger_entries", "(x).seq"),
    ("paper_wallets", "(x).id"),
    ("paper_orders", "(x).id"),
    ("paper_fills", "(x).id"),
    ("order_approvals", "(x).id"),
    ("automation_settings", "(x).user_id"),
    ("automation_consents", "(x).id"),
    ("system_flags", "(x).key"),
    ("strategies", "(x).id"),
    ("strategy_runs", "(x).id"),
    ("backtests", "(x).id"),
    ("api_keys", "(x).id"),
    ("provider_keys", "(x).id"),
    ("protection_arms", "(x).id"),
    ("protection_profiles", "(x).id"),
    ("protection_events", "(x).id"),
    ("market_snapshots", "(x).chain, (x).token_address"),
    ("market_candles", "(x).chain, (x).token_address, (x).timeframe, (x).open_time"),
    ("market_events", "(x).source_name, (x).pair, (x).source_observed_at"),
    ("market_outbox", "(x).id"),
    ("alembic_version", "(x).version_num"),
)

# Tables that carry the ledger and the evidence a restore is actually judged on. A
# missing one is a failed drill even when every other table matches.
LEDGER_TABLES = (
    "ledger_entries",
    "paper_orders",
    "paper_fills",
    "paper_wallets",
    "market_snapshots",
)


def _table_digest(conn: Any, table: str, order_by: str) -> dict[str, Any]:
    """One table's count and a digest over its rows in canonical order.

    The composite ``x::text`` is what gets hashed, and a ``timestamptz`` renders in the
    session's time zone. Pinning UTC here rather than at each caller keeps two readings of
    the same row — one from a live database, one from a restore made by ``pg_restore`` —
    comparable, whatever zone the client happened to have.
    """
    conn.execute("SET TIME ZONE 'UTC'")
    exists = conn.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",)).fetchone()
    if not exists or not exists[0]:
        return {"rows": None, "digest": None, "missing": True}
    count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    digest = conn.execute(
        "SELECT md5(coalesce(string_agg(quote_nullable(x::text), E'\\n' ORDER BY "
        f"{order_by}), '')) FROM {table} x"
    ).fetchone()[0]
    return {"rows": int(count), "digest": digest, "missing": False}


def _connect(target: PgTarget) -> Any:
    return target.connect()


def alembic_version(conn: Any) -> str | None:
    """The applied revision, or None when the database was never stamped.

    A database without the table is a real answer ("no schema"), not a crash: ``verify``
    and ``restore`` both report it through ``_schema_ok`` rather than raising.
    """
    exists = conn.execute("SELECT to_regclass('public.alembic_version') IS NOT NULL").fetchone()
    if not exists or not exists[0]:
        return None
    row = conn.execute(
        "SELECT version_num FROM alembic_version ORDER BY version_num LIMIT 1"
    ).fetchone()
    return None if row is None else str(row[0])


def table_digests(target: PgTarget) -> dict[str, Any]:
    """Every table's count and row digest, read from one connection."""
    conn = _connect(target)
    try:
        tables = table_digests_on(conn)
        version = alembic_version(conn)
    finally:
        conn.close()
    return {"database": target.redact(), "alembic_version": version, "tables": tables}


def table_digests_on(conn: Any) -> dict[str, Any]:
    """Every table's count and row digest, read through an already-open connection.

    The backup calls this inside the transaction that exported its ``pg_dump`` snapshot,
    so the recorded fingerprint and the dump describe exactly the same rows.
    """
    return {table: _table_digest(conn, table, order_by) for table, order_by in TABLES}


def fingerprint_digest(tables: dict[str, Any]) -> str:
    """One short identifier for a set of table digests, for evidence and manifests."""
    canonical = json.dumps(tables, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _report_counts(tables: dict[str, Any]) -> dict[str, int]:
    return {
        name: int(entry["rows"])
        for name, entry in tables.items()
        if entry.get("rows") is not None and entry["rows"] > 0
    }


def compare_tables(
    expected: dict[str, Any], actual: dict[str, Any], *, only: tuple[str, ...] | None = None
) -> list[str]:
    """Differences between two digest sets, as operator-readable lines."""
    names = only if only is not None else tuple(expected)
    problems: list[str] = []
    for name in names:
        want = expected.get(name, {"missing": True, "rows": None, "digest": None})
        got = actual.get(name, {"missing": True, "rows": None, "digest": None})
        if want.get("missing") and got.get("missing"):
            continue
        if want.get("missing") != got.get("missing"):
            problems.append(
                f"{name}: {'missing in source' if want.get('missing') else 'missing in restore'}"
            )
            continue
        if want["rows"] != got["rows"]:
            problems.append(f"{name}: {want['rows']} row(s) in source, {got['rows']} in restore")
            continue
        if want["digest"] != got["digest"]:
            problems.append(f"{name}: same row count ({want['rows']}) but different rows")
    return problems


def ledger_counts(tables: dict[str, Any]) -> dict[str, int]:
    """Row counts for the tables a reader cares about, dropping empty ones."""
    return _report_counts(tables)


@dataclass(frozen=True)
class ReconciliationResult:
    ok: bool
    detail: str
    totals: dict[str, Any]
    checks: list[dict[str, Any]]


def reconcile(database_url: str) -> ReconciliationResult:
    """Run the API's reconciliation report against one database, read-only."""
    engine = create_engine(database_url, future=True)
    try:
        with Session(engine) as session:
            report = reconciliation(session)
    finally:
        engine.dispose()
    failing = [check for check in report["checks"] if not check["ok"]]
    if failing:
        detail = "; ".join(f"{check['id']}: {check['detail']}" for check in failing)
    else:
        detail = f"{len(report['checks'])} checks pass"
    return ReconciliationResult(
        ok=bool(report["ok"]), detail=detail, totals=report["totals"], checks=report["checks"]
    )


__all__ = [
    "LEDGER_TABLES",
    "TABLES",
    "alembic_version",
    "ReconciliationResult",
    "compare_tables",
    "fingerprint_digest",
    "ledger_counts",
    "reconcile",
    "table_digests",
    "table_digests_on",
]
