#!/usr/bin/env python3
"""Fail the build on a pin with a known advisory, instead of printing a report.

The verdict is a function of an advisory report and an allowlist, so it can be
replayed offline and tested without a network:

* ``--python`` audits ``requirements.lock`` and ``requirements-runtime.lock`` with
  ``pip-audit`` against its advisory service (OSV or PyPI).
* ``--node`` audits the pnpm workspace with ``pnpm audit``.
* ``--report FILE`` reads a saved ``pip-audit --format json`` report and applies
  the same verdict, which is how an air-gapped machine evaluates a report
  produced elsewhere.

A vulnerability whose id is not in ``tools/pins/audit-allowlist.txt`` exits 1,
including a pin that is vulnerable and matches no entry. An allowlisted id must
carry a reason; an entry without one is itself an error, so the list cannot be
used as a mute button.

Exit 2 means the audit could not run at all (tool absent, service unreachable,
report unreadable). It is never 0: "I did not check" is not "clean".

Usage:
    python3.11 tools/audit_pins.py --python
    python3.11 tools/audit_pins.py --node
    python3.11 tools/audit_pins.py --report /tmp/pip-audit.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ALLOWLIST = ROOT / "tools" / "pins" / "audit-allowlist.txt"
PYTHON_LOCKS = (
    "requirements-runtime.lock",
    "requirements.lock",
    "requirements-keyring.lock",
)


class AuditUnavailable(RuntimeError):
    """The audit tool or its advisory source could not be used."""


def load_allowlist(path: Path) -> dict[str, str]:
    """Advisory id -> reason. An entry without a reason is refused."""
    allowed: dict[str, str] = {}
    if not path.is_file():
        return allowed
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or not parts[1].strip():
            raise AuditUnavailable(f"{path}:{number}: the entry needs an id and a reason")
        allowed[parts[0]] = parts[1].strip()
    return allowed


def _ids(entry: dict[str, object]) -> set[str]:
    """Every identifier an advisory reports, so an alias cannot slip past."""
    found: set[str] = set()
    for key in ("id", "aliases", "vulnerability_id"):
        value = entry.get(key)
        if isinstance(value, str):
            found.add(value)
        elif isinstance(value, list):
            found.update(item for item in value if isinstance(item, str))
    return found


def python_verdict(report: dict[str, object], allowed: dict[str, str]) -> list[str]:
    """Unallowlisted advisories in a ``pip-audit --format json`` report."""
    errors: list[str] = []
    seen: set[tuple[str, str, frozenset[str]]] = set()
    dependencies = report.get("dependencies")
    if not isinstance(dependencies, list):
        errors.append("audit: the report has no dependencies array")
        return errors
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            errors.append("audit: a dependency entry is not an object")
            continue
        name = str(dependency.get("name", "?"))
        version = str(dependency.get("version", "?"))
        vulns = dependency.get("vulns") or []
        if not isinstance(vulns, list):
            errors.append(f"audit: {name}=={version} has a malformed vulns entry")
            continue
        for entry in vulns:
            if not isinstance(entry, dict):
                errors.append(f"audit: {name}=={version} has a malformed advisory")
                continue
            ids = _ids(entry)
            if ids & allowed.keys():
                continue
            key = (name, version, frozenset(ids))
            if key in seen:
                continue
            seen.add(key)
            fixes = entry.get("fix_versions") or []
            target = f" (fixed in {', '.join(map(str, fixes))})" if fixes else ""
            errors.append(f"vulnerable pin: {name}=={version} {', '.join(sorted(ids))}{target}")
    return errors


def node_verdict(report: dict[str, object], allowed: dict[str, str]) -> list[str]:
    """Unallowlisted advisories in a ``pnpm audit --json`` report."""
    errors: list[str] = []
    seen: set[tuple[str, frozenset[str]]] = set()
    advisories = report.get("advisories")
    if advisories is None:
        errors.append("audit: the pnpm report has no advisories object")
        return errors
    if not isinstance(advisories, dict):
        errors.append("audit: the pnpm advisories entry is not an object")
        return errors
    for key, advisory in advisories.items():
        if not isinstance(advisory, dict):
            errors.append(f"audit: advisory {key} is malformed")
            continue
        ids = _ids(advisory)
        ids.add(str(key))
        github = [item for item in advisory.get("references", "").split() if "GHSA-" in item]
        ids.update(reference.rsplit("/", 1)[-1] for reference in github)
        ids.add(str(advisory.get("github_advisory_id") or ""))
        ids.discard("")
        if ids & allowed.keys():
            continue
        module = advisory.get("module", "?")
        severity = advisory.get("severity", "?")
        versions = advisory.get("vulnerable_versions", "?")
        key = (str(module), frozenset(ids))
        if key in seen:
            continue
        seen.add(key)
        title = str(advisory.get("title", ""))[:80]
        errors.append(
            f"vulnerable pin: {module} {versions} ({severity}) {', '.join(sorted(ids))} — {title}"
        )
    return errors


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True)


def _parse_report(text: str, source: str) -> dict[str, object]:
    try:
        report = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AuditUnavailable(f"audit: {source} did not print JSON: {exc}") from exc
    if not isinstance(report, dict):
        raise AuditUnavailable(f"audit: {source} printed JSON that is not an object")
    return report


def run_python(python: str) -> dict[str, object]:
    """Audit the Python locks with pip-audit, or say why that was impossible."""
    if shutil.which(python) is None:
        raise AuditUnavailable(f"audit: {python} is not on PATH")
    probe = _run([python, "-m", "pip_audit", "--version"])
    if probe.returncode != 0:
        raise AuditUnavailable(
            "audit: pip-audit is not installed; "
            f"{python} -m pip install 'pip-audit>=2,<3' (its advisory service needs a network)"
        )
    merged: dict[str, object] = {"dependencies": []}
    dependencies: list[object] = []
    for lock in PYTHON_LOCKS:
        result = _run(
            [
                python,
                "-m",
                "pip_audit",
                "-r",
                str(ROOT / lock),
                "--format",
                "json",
                "--progress-spinner",
                "off",
                "--strict",
            ]
        )
        report = _parse_report(result.stdout, f"pip-audit on {lock}")
        if result.returncode not in (0, 1):
            raise AuditUnavailable(
                f"audit: pip-audit on {lock} exited {result.returncode}: "
                f"{result.stderr.strip()[:300]}"
            )
        entries = report.get("dependencies") or []
        if isinstance(entries, list):
            dependencies.extend(entries)
    merged["dependencies"] = dependencies
    return merged


def run_node() -> dict[str, object]:
    """Audit the pnpm workspace, or say why that was impossible."""
    if shutil.which("pnpm") is None:
        raise AuditUnavailable(
            "audit: pnpm is not on PATH; install pnpm@9 (the registry audit needs a network)"
        )
    result = _run(["pnpm", "audit", "--json"])
    if result.returncode not in (0, 1) or not result.stdout.strip():
        raise AuditUnavailable(
            f"audit: pnpm audit exited {result.returncode}: {result.stderr.strip()[:300]}"
        )
    return _parse_report(result.stdout, "pnpm audit")


def report(errors: Iterable[str], source: str, allowed: dict[str, str]) -> int:
    problems = list(errors)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(f"audit: FAILED for {source}", file=sys.stderr)
        return 1
    suffix = f", {len(allowed)} allowlisted" if allowed else ""
    print(f"audit: ok — no unallowlisted advisory for {source}{suffix}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--python", action="store_true", help="audit the Python locks")
    mode.add_argument("--node", action="store_true", help="audit the pnpm workspace")
    mode.add_argument("--report", type=Path, help="judge a saved pip-audit JSON report")
    parser.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST)
    parser.add_argument(
        "--python-executable", default=sys.executable, help="interpreter that has pip-audit"
    )
    args = parser.parse_args(argv)
    try:
        allowed = load_allowlist(args.allowlist)
        if args.report is not None:
            text = args.report.read_text(encoding="utf-8")
            return report(
                python_verdict(_parse_report(text, "report"), allowed), "the report", allowed
            )
        if args.node:
            return report(node_verdict(run_node(), allowed), "pnpm-lock.yaml", allowed)
        return report(
            python_verdict(run_python(args.python_executable), allowed),
            "the Python locks",
            allowed,
        )
    except AuditUnavailable as exc:
        print(exc, file=sys.stderr)
        print("audit: not run — this is not a pass", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"audit: {exc}", file=sys.stderr)
        print("audit: not run — this is not a pass", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
