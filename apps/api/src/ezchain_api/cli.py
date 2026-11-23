"""``ezchain-api`` command line: migration and read-only readiness entry points.

The readiness command answers the questions an operator otherwise assembles from several
documents: is a deployment token set, are reads open, is the schema at the migration head,
and is there a recent backup whose dump still matches its manifest. It only reads. It never
prints a secret value, a database URL or a raw exception.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import Settings, get_settings
from .migrate import run_migrations

DEFAULT_BIND = "127.0.0.1"
DEFAULT_MAX_BACKUP_AGE_HOURS = 24.0
MANIFEST_TOOL = "tools.ops.backup"
MANIFEST_FORMAT = 1


@dataclass(frozen=True)
class BackupCheck:
    """What the manifest directory says about the newest verified backup."""

    status: str  # verified | stale | unverified | none
    created_at: str | None = None
    age_hours: float | None = None


@dataclass(frozen=True)
class Readiness:
    """Read-only facts plus the reasons this deployment is not ready."""

    listener: str
    loopback: bool
    token_set: bool
    reads_require_auth: bool
    applied: str | None
    head: str | None
    backup: BackupCheck
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems

    def lines(self) -> list[str]:
        lines = [
            "ezchain first-run readiness",
            f"listener: {self.listener} ({'loopback' if self.loopback else 'non-loopback'})",
            f"deployment token: {'set' if self.token_set else 'unset'}",
            f"read authentication: {'required' if self.reads_require_auth else 'open'}",
        ]
        if self.applied is None:
            lines.append("migration: unknown (applied revision not readable)")
        elif self.head is None:
            lines.append(f"migration: unknown (head not readable, applied {self.applied})")
        elif self.applied == self.head:
            lines.append(f"migration: head ({self.applied})")
        else:
            lines.append(f"migration: behind ({self.applied} -> {self.head})")

        if self.backup.status == "verified":
            lines.append(
                f"backup: verified ({self.backup.age_hours:g} hours old, {self.backup.created_at})"
            )
        elif self.backup.status == "stale":
            lines.append(f"backup: stale ({self.backup.age_hours:g} hours old)")
        else:
            lines.append(f"backup: {self.backup.status}")

        lines.extend(f"problem: {problem}" for problem in self.problems)
        lines.append(f"result: {'READY' if self.ok else 'NOT READY'}")
        return lines


def _is_loopback(bind: str) -> bool:
    """A bare address or ``address:port``; unknown spellings are non-loopback."""
    host = bind.strip()
    if host.count(":") == 1:
        host = host.rsplit(":", 1)[0]
    if host.lower() in {"localhost", "ip6-localhost"}:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_manifest(path: Path, now: datetime) -> tuple[datetime, str, timedelta] | None:
    """Accept only a backup manifest whose dump still matches and whose age is known."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    if document.get("tool") != MANIFEST_TOOL or document.get("format_version") != MANIFEST_FORMAT:
        return None
    dump = document.get("dump")
    if not isinstance(dump, dict):
        return None
    name, recorded = dump.get("path"), dump.get("sha256")
    if not isinstance(name, str) or not isinstance(recorded, str) or len(recorded) != 64:
        return None
    try:
        int(recorded, 16)
    except ValueError:
        return None
    tables = document.get("tables")
    if not isinstance(tables, dict) or not tables:
        return None

    dump_path = path.parent / name
    if dump_path.parent != path.parent:
        return None
    try:
        actual = _sha256_file(dump_path)
    except OSError:
        return None
    if not hmac.compare_digest(actual, recorded.lower()):
        return None

    created_raw = document.get("created_at")
    if not isinstance(created_raw, str):
        return None
    try:
        created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    age = now - created
    if age < timedelta(0):
        return None
    return created, created_raw, age


def _verify_backups(backup_dir: Path, max_age: timedelta, now: datetime) -> BackupCheck:
    """The newest verified manifest in ``backup_dir``, or why there is none."""
    try:
        manifests = sorted(backup_dir.glob("*.manifest.json"))
    except OSError:
        manifests = []
    best: tuple[datetime, str, timedelta] | None = None
    for manifest in manifests:
        verified = _verify_manifest(manifest, now)
        if verified is None:
            continue
        created, created_raw, age = verified
        if best is None or created > best[0]:
            best = (created, created_raw, age)
    if best is None:
        status = "unverified" if manifests else "none"
        return BackupCheck(status)
    _created, created_raw, age = best
    status = "verified" if age <= max_age else "stale"
    return BackupCheck(status, created_raw, round(age.total_seconds() / 3600, 1))


def build_readiness(
    settings: Settings,
    *,
    bind: str,
    backup_dir: Path,
    max_backup_age_hours: float = DEFAULT_MAX_BACKUP_AGE_HOURS,
    applied_revision: Callable[[], str | None],
    head_revision: Callable[[], str | None],
    now: datetime | None = None,
) -> Readiness:
    """Compute the report from injected facts so the command stays testable and read-only."""
    moment = now or datetime.now(UTC)
    try:
        applied = applied_revision()
    except Exception:  # noqa: BLE001 - a failed probe is a fact, never a leaked traceback
        applied = None
    try:
        head = head_revision()
    except Exception:  # noqa: BLE001 - same: report the absence, not the exception text
        head = None

    loopback = _is_loopback(bind)
    token_set = settings.api_token is not None and bool(settings.api_token.get_secret_value())
    reads_require_auth = bool(settings.require_auth_for_reads and settings.auth_enabled)
    backup = _verify_backups(backup_dir, timedelta(hours=max_backup_age_hours), moment)

    problems: list[str] = []
    if not loopback and not token_set:
        problems.append("a non-loopback listener requires EZCHAIN_API_TOKEN")
    if applied is None:
        problems.append("the applied schema revision cannot be read")
    elif head is None:
        problems.append("the migration head cannot be read")
    elif applied != head:
        problems.append(f"schema is behind the migration head ({applied} -> {head})")
    if backup.status in {"none", "unverified"}:
        problems.append("no verified backup was found")
    elif backup.status == "stale":
        problems.append(f"the newest verified backup is older than {max_backup_age_hours:g} hours")

    return Readiness(
        listener=bind,
        loopback=loopback,
        token_set=token_set,
        reads_require_auth=reads_require_auth,
        applied=applied,
        head=head,
        backup=backup,
        problems=tuple(problems),
    )


def run_readiness(
    settings: Settings,
    *,
    bind: str,
    backup_dir: Path,
    max_backup_age_hours: float,
    applied_revision: Callable[[], str | None],
    head_revision: Callable[[], str | None],
) -> int:
    """Print the report and return the exit code: 0 ready, 1 at least one failed check."""
    report = build_readiness(
        settings,
        bind=bind,
        backup_dir=backup_dir,
        max_backup_age_hours=max_backup_age_hours,
        applied_revision=applied_revision,
        head_revision=head_revision,
    )
    for line in report.lines():
        print(line)
    return 0 if report.ok else 1


def _probe_applied_revision(database_url: str) -> str | None:
    """Read the Alembic version row without creating a database or changing a row."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    parsed = make_url(database_url)
    if parsed.get_backend_name() == "sqlite":
        name = parsed.database
        if name and name != ":memory:" and not Path(name).is_file():
            return None
    engine = create_engine(database_url, pool_pre_ping=True, future=True)
    try:
        with engine.connect() as connection:
            value = connection.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            ).scalar()
    finally:
        engine.dispose()
    return None if value is None else str(value)


def _probe_head_revision() -> str | None:
    from .db import head_revision

    return head_revision()


def _readiness(args: argparse.Namespace) -> int:
    try:
        settings = get_settings()
    except Exception:  # noqa: BLE001 - a config error is a refusal; never echo its values
        print("ezchain first-run readiness")
        print("problem: configuration cannot be loaded")
        print("result: NOT READY")
        return 1

    bind = args.bind or os.environ.get("EZCHAIN_BIND", DEFAULT_BIND)
    configured_dir = args.backup_dir or os.environ.get("EZCHAIN_BACKUP_DIR")
    backup_dir = Path(configured_dir) if configured_dir else Path.cwd() / "backups"
    max_age = (
        args.max_backup_age_hours
        if args.max_backup_age_hours is not None
        else DEFAULT_MAX_BACKUP_AGE_HOURS
    )
    return run_readiness(
        settings,
        bind=bind,
        backup_dir=backup_dir,
        max_backup_age_hours=max_age,
        applied_revision=lambda: _probe_applied_revision(settings.database_url),
        head_revision=_probe_head_revision,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ezchain-api")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="apply Alembic migrations up to head")
    ready = commands.add_parser(
        "readiness", help="report first-run readiness without changing anything"
    )
    ready.add_argument("--bind", default=None, help="listener address (EZCHAIN_BIND or 127.0.0.1)")
    ready.add_argument(
        "--backup-dir", default=None, help="directory of backup manifests (EZCHAIN_BACKUP_DIR)"
    )
    ready.add_argument(
        "--max-backup-age-hours",
        type=float,
        default=None,
        help=f"oldest acceptable backup in hours (default {DEFAULT_MAX_BACKUP_AGE_HOURS:g})",
    )
    args = parser.parse_args(argv)

    if args.command == "migrate":
        run_migrations()
        return 0
    if args.command == "readiness":
        return _readiness(args)
    parser.error(f"unknown command: {args.command}")
    return 2  # pragma: no cover - argparse exits


if __name__ == "__main__":
    raise SystemExit(main())
