"""``ezchain-api`` command line: migration entry point for the deploy contract."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from .migrate import run_migrations


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ezchain-api")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="apply Alembic migrations up to head")
    args = parser.parse_args(argv)

    if args.command == "migrate":
        run_migrations()
        return 0
    parser.error(f"unknown command: {args.command}")
    return 2  # pragma: no cover - argparse exits


if __name__ == "__main__":
    raise SystemExit(main())
