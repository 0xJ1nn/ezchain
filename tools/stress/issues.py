"""Deduplicated GitHub issue filing for stress findings.

The fingerprint deliberately excludes timings, counts and response bodies: one fingerprint
per (code, scenario, invariant). Before creating anything the harness lists open issues
labelled ``stress`` and skips any body that already carries the fingerprint marker, so a
re-run creates exactly one issue per fingerprint. Filing is opt-in and never happens for a
passing run.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TextIO, cast

from .checks import Finding

FINGERPRINT_RE = re.compile(r"<!--\s*stress-fingerprint:\s*([0-9a-f]{64})\s*-->")
LABELS = ("stress", "bug")


class IssueFilingError(RuntimeError):
    """``gh`` is missing, unauthenticated, or a command failed."""


@dataclass(frozen=True)
class IssuePayload:
    fingerprint: str
    title: str
    body: str
    labels: tuple[str, ...] = LABELS

    def to_dict(self) -> dict[str, object]:
        return {
            "fingerprint": self.fingerprint,
            "title": self.title,
            "labels": list(self.labels),
            "body": self.body,
        }


GhRunner = Callable[[Sequence[str], str | None], subprocess.CompletedProcess[str]]


def fingerprint_for(code: str, scenario: str, invariant: str) -> str:
    """SHA-256 of the stable failure identity. Timings and prose are excluded."""
    payload = f"{code}\x1f{scenario}\x1f{invariant}".encode()
    return hashlib.sha256(payload).hexdigest()


def fingerprint(finding: Finding) -> str:
    return fingerprint_for(finding.code, finding.scenario, finding.invariant)


def marker(fingerprint_value: str) -> str:
    return f"<!-- stress-fingerprint: {fingerprint_value} -->"


def _short_summary(finding: Finding) -> str:
    summary = " ".join(finding.summary.split()) or finding.invariant
    return summary[:80]


def _truncate(value: object, limit: int = 2000) -> str:
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... ({len(text) - limit} more characters)"


def _report_value(report: Mapping[str, object], key: str, default: str = "unknown") -> str:
    value = report.get(key)
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def issue_body(finding: Finding, report: Mapping[str, object], fp: str) -> str:
    command = _report_value(report, "command", "python -m tools.stress")
    evidence = _truncate(finding.evidence) if finding.evidence else "(see report artifact)"
    return "\n".join(
        [
            marker(fp),
            "",
            "## Summary",
            "",
            finding.summary,
            "",
            f"Fingerprint: `{fp}`",
            "",
            "## Run context",
            "",
            "| field | value |",
            "|---|---|",
            f"| mode | {_report_value(report, 'mode')} |",
            f"| seed | {_report_value(report, 'seed')} |",
            f"| base URL | {_report_value(report, 'base_url')} |",
            f"| chain | {_report_value(report, 'chain')} |",
            f"| token | {_report_value(report, 'token_address')} |",
            f"| commit | {_report_value(report, 'git_commit')} |",
            f"| report | {_report_value(report, 'report_path')} |",
            "",
            "## Expected",
            "",
            finding.expected,
            "",
            "## Actual",
            "",
            finding.actual,
            "",
            "## Evidence",
            "",
            "```text",
            evidence,
            "```",
            "",
            "## Reproduction",
            "",
            "```bash",
            command,
            "```",
            "",
            "The command must run against an API whose `/api/v1/paper/account` reports",
            "`mode: paper`. This harness never signs, never handles keys and never calls a",
            "live venue.",
            "",
            f"Labels: {', '.join(LABELS)}",
            "",
        ]
    )


def issue_payload(finding: Finding, report: Mapping[str, object]) -> IssuePayload:
    fp = fingerprint(finding)
    return IssuePayload(
        fingerprint=fp,
        title=f"[stress] {finding.code}: {_short_summary(finding)}",
        body=issue_body(finding, report, fp),
    )


def existing_fingerprints(issues: Sequence[Mapping[str, object]]) -> set[str]:
    """Extract fingerprint markers from ``gh issue list --json number,title,body`` rows."""
    found: set[str] = set()
    for issue in issues:
        body = issue.get("body")
        if isinstance(body, str):
            found.update(FINGERPRINT_RE.findall(body))
    return found


def plan_issue_payloads(
    findings: Sequence[Finding],
    report: Mapping[str, object],
    existing: set[str],
) -> list[IssuePayload]:
    """One payload per fingerprint, skipping fingerprints already present in open issues."""
    payloads: list[IssuePayload] = []
    seen = set(existing)
    for finding in findings:
        payload = issue_payload(finding, report)
        if payload.fingerprint in seen:
            continue
        seen.add(payload.fingerprint)
        payloads.append(payload)
    return payloads


def _run(
    args: Sequence[str],
    input_text: str | None,
    runner: GhRunner | None,
) -> subprocess.CompletedProcess[str]:
    if runner is not None:
        return runner(args, input_text)
    return subprocess.run(
        list(args),
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )


def _require_gh(gh_path: str | None) -> str:
    path = gh_path or shutil.which("gh")
    if path is None:
        raise IssueFilingError(
            "gh is not installed or not on PATH; install GitHub CLI and run 'gh auth login'. "
            "The stress report was still written."
        )
    return path


def fetch_existing_fingerprints(
    repo: str,
    *,
    runner: GhRunner | None = None,
    gh_path: str | None = None,
) -> set[str]:
    """List open ``stress`` issues and return the fingerprints already present."""
    gh = _require_gh(gh_path)
    args = [
        gh,
        "issue",
        "list",
        "--repo",
        repo,
        "--label",
        "stress",
        "--state",
        "open",
        "--limit",
        "200",
        "--json",
        "number,title,body",
    ]
    completed = _run(args, None, runner)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise IssueFilingError(
            "gh issue list failed; check 'gh auth status' and the --repo value. "
            f"Report kept. Detail: {detail or 'no output'}"
        )
    raw = completed.stdout.strip()
    if not raw:
        return set()
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IssueFilingError(f"gh issue list returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise IssueFilingError("gh issue list returned JSON that is not a list")
    issues: list[Mapping[str, object]] = []
    for item in cast(Sequence[object], parsed):
        if isinstance(item, Mapping):
            issues.append(cast(Mapping[str, object], item))
    return existing_fingerprints(issues)


def create_issue(
    payload: IssuePayload,
    repo: str,
    *,
    runner: GhRunner | None = None,
    gh_path: str | None = None,
) -> str:
    """Create one issue; body goes on stdin via ``--body-file -``."""
    gh = _require_gh(gh_path)
    args = [
        gh,
        "issue",
        "create",
        "--repo",
        repo,
        "--label",
        "stress",
        "--label",
        "bug",
        "--title",
        payload.title,
        "--body-file",
        "-",
    ]
    completed = _run(args, payload.body, runner)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise IssueFilingError(
            f"gh issue create failed for {payload.fingerprint}: {detail or 'no output'}"
        )
    return completed.stdout.strip()


def file_findings(
    findings: Sequence[Finding],
    report: Mapping[str, object],
    repo: str | None,
    *,
    dry_run: bool,
    runner: GhRunner | None = None,
    gh_path: str | None = None,
    out: TextIO | None = None,
) -> list[str]:
    """Plan and (unless dry-run) create issues. Returns created URLs.

    A passing run has no findings, so this function always returns ``[]`` for it. Dedupe
    happens twice: inside the batch and against existing open issues.
    """
    stream = out or sys.stdout
    if not findings:
        return []
    if not repo:
        raise IssueFilingError("--repo owner/name is required when filing or dry-running issues")
    if dry_run:
        payloads = plan_issue_payloads(findings, report, set())
        print(json.dumps([payload.to_dict() for payload in payloads], indent=2), file=stream)
        print(
            f"dry-run: {len(payloads)} issue payload(s) printed; nothing was created",
            file=sys.stderr,
        )
        return []
    gh = _require_gh(gh_path)
    existing = fetch_existing_fingerprints(repo, runner=runner, gh_path=gh)
    payloads = plan_issue_payloads(findings, report, existing)
    if not payloads:
        print("no issues filed: every fingerprint is already open", file=sys.stderr)
        return []
    urls: list[str] = []
    for payload in payloads:
        url = create_issue(payload, repo, runner=runner, gh_path=gh)
        urls.append(url)
        print(f"filed {payload.fingerprint[:12]} {url}", file=sys.stderr)
    return urls
