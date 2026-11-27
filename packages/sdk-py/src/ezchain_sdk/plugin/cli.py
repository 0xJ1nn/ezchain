"""List and validate plugins without loading their code.

Exit codes: 0 = no issues, 1 = one or more invalid plugins, 2 = bad invocation.
Run with ``python -m ezchain_sdk.plugin.cli list --path /srv/ezchain/plugins``.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from .loader import discover_plugins

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ezchain_sdk.plugin.cli",
        description="Discover and validate plugin manifests; never imports plugin code.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name, description in (
        ("list", "list validated plugin records"),
        ("validate", "validate plugin manifests and report issues"),
    ):
        command = commands.add_parser(name, help=description)
        command.add_argument(
            "--path",
            action="append",
            dest="paths",
            required=True,
            metavar="DIR",
            help="plugin directory (repeat for several roots)",
        )
        command.add_argument(
            "--host-version",
            default=None,
            metavar="VERSION",
            help="reject a plugin whose compatibility range excludes this version",
        )
        command.add_argument(
            "--json", action="store_true", help="emit the full discovery result as JSON"
        )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code, never raises for bad input."""
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if exc.code == 0 else EXIT_USAGE

    result = discover_plugins(args.paths, host_version=args.host_version)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        for record in result.records:
            print(
                f"OK {record.plugin_id} {record.manifest.version} "
                f"{record.manifest.kind} {record.manifest_path}"
            )
        for issue in result.issues:
            print(f"issue {issue.code} {issue.path}: {issue.message}")
    return EXIT_INVALID if result.issues else EXIT_OK


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())


__all__ = ["EXIT_INVALID", "EXIT_OK", "EXIT_USAGE", "build_parser", "main"]
