"""``python -m tools.ops`` — backup, restore, the timed restore rehearsal and the drill.

Exit codes are part of the contract, because the runbook and any future cron both read
them:

* ``0`` — the command did what it says and every verification passed;
* ``1`` — a verification failed (different rows, a failed reconciliation, a short restore,
  a drill phase that did not pass);
* ``2`` — the command refused to run, or an external tool failed (no Docker daemon, an
  unusable env file, a refused target).

``--json`` prints the machine-readable document; the default output is a short operator
summary. Nothing printed contains a password.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .backup import backup
from .drill import DEFAULT_CHAIN, DEFAULT_PROJECT, DEFAULT_TOKEN, drill, migration_chain
from .pg import OpsError, PgTarget
from .rehearse import rehearse, write_report
from .restore import drop_target, restore, verify_fingerprint

EXIT_REFUSED = 2
ROOT = Path(__file__).resolve().parents[2]


def _print(payload: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            print(f"{key}: {json.dumps(value, sort_keys=True)}")
        else:
            print(f"{key}: {value}")


def _backup(args: argparse.Namespace) -> int:
    result = backup(
        args.database,
        Path(args.dest),
        label=args.label,
        force=args.force,
        pg_dump_bin=args.pg_dump,
    )
    _print(result.summary(), getattr(args, "json", False))
    print(f"backup: ok ({'reused' if result.reused else 'written'})")
    return 0


def _restore(args: argparse.Namespace) -> int:
    result = restore(
        args.database,
        Path(args.manifest),
        dump=None if args.dump is None else Path(args.dump),
        pg_restore_bin=args.pg_restore,
    )
    _print(result.summary(), getattr(args, "json", False))
    if not result.ok:
        print(f"restore: FAILED — {len(result.differences)} difference(s)", file=sys.stderr)
        for line in result.differences:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print(f"restore: ok — {result.counts.get('ledger_entries', 0)} ledger entries reproduced")
    return 0


def _verify(args: argparse.Namespace) -> int:
    report = verify_fingerprint(args.database, Path(args.manifest))
    _print(report, getattr(args, "json", False))
    if not report["matches"]:
        print(f"verify: FAILED — {len(report['differences'])} difference(s)", file=sys.stderr)
        for line in report["differences"]:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print("verify: ok — rows match the manifest")
    return 0


def _rehearse(args: argparse.Namespace) -> int:
    result = rehearse(
        args.admin_database,
        Path(args.dest),
        drop_source=not args.keep_source,
        keep_databases=args.keep_databases,
        pg_dump_bin=args.pg_dump,
        pg_restore_bin=args.pg_restore,
    )
    if args.report:
        write_report(result, Path(args.report))
    summary = {
        "source": result.source,
        "target": result.target,
        "dropped_source": result.dropped_source,
        "seed": {
            "orders": result.seed.orders,
            "fills": result.seed.fills,
            "replayed": result.seed.replayed,
        },
        "backup": result.backup.summary(),
        "timings": result.timings,
        "restore": result.restore.summary(),
        "report": args.report,
    }
    _print(summary, getattr(args, "json", False))
    if not result.ok:
        print(
            f"rehearse: FAILED — {len(result.restore.differences)} difference(s)", file=sys.stderr
        )
        for line in result.restore.differences:
            print(f"  - {line}", file=sys.stderr)
        return 1
    seconds = result.timings.get("recovery_seconds_rounded")
    print(f"rehearse: ok — recovery {seconds}s, reconciliation ok")
    return 0


def _drop(args: argparse.Namespace) -> int:
    drop_target(args.database)
    print(f"drop: ok — {PgTarget.parse(args.database).redact()} removed")
    return 0


def _drill(args: argparse.Namespace) -> int:
    result = drill(
        root=ROOT,
        compose=Path(args.compose),
        env_file=Path(args.env_file),
        project=args.project,
        base_url=args.base_url,
        docker=args.docker,
        timeout=args.timeout,
        build_timeout=args.build_timeout,
        dest=None if args.dest is None else Path(args.dest),
        keep=args.keep,
        static_only=args.static_only,
        chain=args.chain,
        token=args.token,
    )
    if getattr(args, "json", False):
        # The machine-readable document is the whole of stdout: a caller parses it with
        # json.load, so the operator's one-line verdict goes to stderr.
        _print(result.document, True)
        stream = sys.stderr
    else:
        stream = sys.stdout
        for phase in result.phases:
            print(f"  {phase.status:<7} {phase.name}: {phase.detail}")
    if result.status == "passed":
        print(
            f"drill: ok ({result.document['mode']}) at {result.document['head_revision']}",
            file=stream,
        )
        return 0
    if result.status == "skipped":
        print(f"drill: NOT RUN — {result.document['refusal']}", file=sys.stderr)
        print("drill: this is a refusal, not a pass", file=sys.stderr)
        return EXIT_REFUSED
    print(f"drill: FAILED — {result.document['failure']}", file=sys.stderr)
    return 1


def _chain(args: argparse.Namespace) -> int:
    head, count, problems = migration_chain()
    document = {"head": head, "revisions": count, "problems": problems}
    stream = sys.stderr if getattr(args, "json", False) else sys.stdout
    _print(document, getattr(args, "json", False))
    if problems:
        print(f"chain: FAILED — {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(f"chain: ok — {count} revisions, head {head}", file=stream)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.ops")
    # ``--json`` is accepted before or after the subcommand: an operator writing a
    # one-liner should not have to remember which side of the verb it goes on.
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="print the machine-readable document",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="print the machine-readable document",
    )

    p_backup = sub.add_parser(
        "backup", parents=[common], help="dump a database and record a ledger fingerprint"
    )
    p_backup.add_argument("--database", required=True, help="source Postgres URL")
    p_backup.add_argument("--dest", required=True, help="directory for <label>.dump and manifest")
    p_backup.add_argument("--label", default=None, help="dump name (default: database name)")
    p_backup.add_argument("--force", action="store_true", help="take a new dump even if one exists")
    p_backup.add_argument("--pg-dump", default=None, help="path to pg_dump")
    p_backup.set_defaults(func=_backup)

    p_restore = sub.add_parser(
        "restore", parents=[common], help="load a backup into a fresh disposable database"
    )
    p_restore.add_argument(
        "--database", required=True, help="target URL (name must look disposable)"
    )
    p_restore.add_argument("--manifest", required=True, help="path to the .manifest.json")
    p_restore.add_argument("--dump", default=None, help="override the dump path")
    p_restore.add_argument("--pg-restore", default=None, help="path to pg_restore")
    p_restore.set_defaults(func=_restore)

    p_verify = sub.add_parser(
        "verify", parents=[common], help="compare an existing database to a manifest"
    )
    p_verify.add_argument("--database", required=True, help="database URL")
    p_verify.add_argument("--manifest", required=True, help="path to the .manifest.json")
    p_verify.set_defaults(func=_verify)

    p_rehearse = sub.add_parser(
        "rehearse", parents=[common], help="backup, destroy, restore and time the recovery"
    )
    p_rehearse.add_argument(
        "--admin-database", required=True, help="server URL; databases are created on it"
    )
    p_rehearse.add_argument("--dest", required=True, help="directory for the dump and manifest")
    p_rehearse.add_argument("--report", default=None, help="write the JSON report here")
    p_rehearse.add_argument(
        "--keep-source", action="store_true", help="do not drop the source (not a drill)"
    )
    p_rehearse.add_argument(
        "--keep-databases", action="store_true", help="leave both databases for inspection"
    )
    p_rehearse.add_argument("--pg-dump", default=None, help="path to pg_dump")
    p_rehearse.add_argument("--pg-restore", default=None, help="path to pg_restore")
    p_rehearse.set_defaults(func=_rehearse)

    p_drop = sub.add_parser("drop", parents=[common], help="remove a disposable database")
    p_drop.add_argument(
        "--database", required=True, help="database URL (name must look disposable)"
    )
    p_drop.set_defaults(func=_drop)

    p_drill = sub.add_parser(
        "drill",
        parents=[common],
        help="re-drill the Compose stack at this checkout's migration head",
    )
    p_drill.add_argument(
        "--compose", default=str(ROOT / "infra" / "compose.yaml"), help="compose file to drill"
    )
    p_drill.add_argument(
        "--env-file",
        default=str(ROOT / "infra" / ".env"),
        help="Compose env file holding POSTGRES_PASSWORD and EZCHAIN_API_TOKEN",
    )
    p_drill.add_argument("--project", default=DEFAULT_PROJECT, help="Compose project name")
    p_drill.add_argument(
        "--base-url", default=None, help="edge URL (default: EZCHAIN_PUBLIC_URL from the env file)"
    )
    p_drill.add_argument("--docker", default=None, help="docker binary (default: docker on PATH)")
    p_drill.add_argument(
        "--timeout", type=float, default=180.0, help="seconds to wait for readiness"
    )
    p_drill.add_argument(
        "--build-timeout", type=float, default=1800.0, help="seconds allowed for compose up --build"
    )
    p_drill.add_argument("--dest", default=None, help="directory for the dump (default: temp dir)")
    p_drill.add_argument("--keep", action="store_true", help="leave the project running")
    p_drill.add_argument(
        "--static-only",
        action="store_true",
        help="validate the compose file and migration chain; never start a container",
    )
    p_drill.add_argument("--chain", default=DEFAULT_CHAIN, help="chain for the paper order")
    p_drill.add_argument("--token", default=DEFAULT_TOKEN, help="token for the paper order")
    p_drill.set_defaults(func=_drill)

    p_chain = sub.add_parser(
        "chain", parents=[common], help="check the Alembic chain has exactly one head"
    )
    p_chain.set_defaults(func=_chain)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except OpsError as exc:
        print(f"ops: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
