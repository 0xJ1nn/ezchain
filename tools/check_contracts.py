#!/usr/bin/env python3
"""Check or refresh the versioned contract snapshots.

`--check` is the gate: it regenerates the OpenAPI document, the MCP tool registry and the
SDK interfaces, diffs them against the committed snapshots and exits non-zero with the
changed field paths when the version did not move. `--update` records the current live
version from ``ezchain_api.__version__`` and rewrites the snapshots after a reviewed change.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.contract.capture import SURFACES, capture_all  # noqa: E402
from tests.contract.gate import (  # noqa: E402
    DEPRECATIONS_FILE,
    MANIFEST_FILE,
    SNAPSHOT_DIR,
    evaluate,
)

from ezchain_api import __version__  # noqa: E402


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def update() -> int:
    captured = capture_all()
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    _write_json(
        MANIFEST_FILE,
        {
            "version": __version__,
            "version_source": "ezchain_api.__version__",
            "surfaces": list(SURFACES),
        },
    )
    for surface in SURFACES:
        _write_json(SNAPSHOT_DIR / f"{surface}.json", captured[surface])
    if not DEPRECATIONS_FILE.exists():
        _write_json(DEPRECATIONS_FILE, [])
    print(f"contract snapshots refreshed at version {__version__}")
    return 0


def check() -> int:
    if not MANIFEST_FILE.exists():
        print(f"missing {MANIFEST_FILE}; run tools/check_contracts.py --update", file=sys.stderr)
        return 1
    captured = capture_all()
    manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    previous = {
        surface: json.loads((SNAPSHOT_DIR / f"{surface}.json").read_text(encoding="utf-8"))
        for surface in SURFACES
    }
    deprecations = json.loads(DEPRECATIONS_FILE.read_text(encoding="utf-8"))
    problems = evaluate(captured, previous, manifest, deprecations, __version__)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(f"contract gate: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(f"contract gate: ok (version {__version__}, {len(SURFACES)} surfaces)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare the live surfaces with the snapshots (the default)",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="rewrite the snapshots and record the current API version",
    )
    args = parser.parse_args()
    if args.check and args.update:
        parser.error("--check and --update are mutually exclusive")
    return update() if args.update else check()


if __name__ == "__main__":
    raise SystemExit(main())
