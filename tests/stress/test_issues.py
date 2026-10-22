"""Fingerprint, payload and dedupe tests. No ``gh`` process and no network are used."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence

import pytest
from tools.stress.checks import Finding
from tools.stress.issues import (
    FINGERPRINT_RE,
    IssueFilingError,
    existing_fingerprints,
    file_findings,
    fingerprint,
    fingerprint_for,
    issue_payload,
    marker,
    plan_issue_payloads,
)

REPORT = {
    "mode": "smoke",
    "seed": 7,
    "base_url": "http://127.0.0.1:8000",
    "chain": "base",
    "token_address": "0x00000000000000000000000000000000000000aa",
    "git_commit": "abc123",
    "report_path": "tools/stress/reports/stress-smoke.json",
    "command": "python -m tools.stress --mode smoke --seed 7",
}


def finding(
    *,
    code: str = "UNEXPECTED_5XX",
    scenario: str = "smoke",
    invariant: str = "http.no_unexpected_5xx",
    summary: str = "the API returned a 5xx response",
    expected: str = "no 5xx",
    actual: str = "HTTP 500",
    evidence: str = "Internal Server Error",
) -> Finding:
    return Finding(
        code=code,
        scenario=scenario,
        invariant=invariant,
        summary=summary,
        expected=expected,
        actual=actual,
        evidence=evidence,
    )


def test_fingerprint_is_stable_and_excludes_timing_and_prose() -> None:
    first = finding(summary="slow", actual="HTTP 500 at 10:00", evidence="x" * 10)
    second = finding(summary="different words", actual="HTTP 503", evidence="y" * 10)
    assert fingerprint(first) == fingerprint(second)
    assert fingerprint(first) == fingerprint_for(
        "UNEXPECTED_5XX", "smoke", "http.no_unexpected_5xx"
    )
    assert len(fingerprint(first)) == 64


def test_fingerprint_changes_with_code_scenario_or_invariant() -> None:
    base = fingerprint(finding())
    assert fingerprint(finding(code="OTHER")) != base
    assert fingerprint(finding(scenario="soak")) != base
    assert fingerprint(finding(invariant="http.no_connection_errors")) != base


def test_issue_payload_has_marker_title_labels_and_reproduction() -> None:
    item = finding()
    payload = issue_payload(item, REPORT)
    assert payload.title.startswith("[stress] UNEXPECTED_5XX: ")
    assert marker(payload.fingerprint) in payload.body
    assert FINGERPRINT_RE.findall(payload.body) == [payload.fingerprint]
    assert "Labels: stress, bug" in payload.body
    assert "python -m tools.stress --mode smoke --seed 7" in payload.body
    assert "HTTP 500" in payload.body
    assert payload.labels == ("stress", "bug")


def test_existing_fingerprints_reads_open_issue_bodies() -> None:
    known = fingerprint(finding())
    issues = [
        {"number": 1, "title": "a", "body": f"prefix {marker(known)} suffix"},
        {"number": 2, "title": "b", "body": "no marker"},
        {"number": 3, "title": "c", "body": None},
    ]
    assert existing_fingerprints(issues) == {known}


def test_plan_dedupes_within_batch_and_against_existing() -> None:
    first = finding()
    duplicate = finding(summary="same fingerprint, different words")
    other = finding(code="CONNECTION_ERROR", invariant="http.no_connection_errors")
    existing = {fingerprint(other)}

    payloads = plan_issue_payloads([first, duplicate, other], REPORT, existing)
    assert len(payloads) == 1
    assert payloads[0].fingerprint == fingerprint(first)


def test_passing_run_never_calls_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(gh_path: str | None) -> str:
        raise AssertionError("gh must not be touched when there are no findings")

    monkeypatch.setattr("tools.stress.issues._require_gh", explode)
    assert file_findings([], REPORT, "owner/name", dry_run=False) == []


def test_dry_run_prints_payloads_without_calling_gh(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(gh_path: str | None) -> str:
        raise AssertionError("dry-run must not call gh")

    monkeypatch.setattr("tools.stress.issues._require_gh", explode)
    urls = file_findings([finding()], REPORT, "owner/name", dry_run=True)
    assert urls == []
    captured = capsys.readouterr()
    printed = json.loads(captured.out)
    assert printed[0]["fingerprint"] == fingerprint(finding())
    assert printed[0]["labels"] == ["stress", "bug"]
    assert "nothing was created" in captured.err


def test_file_findings_creates_one_issue_for_duplicate_findings() -> None:
    calls: list[tuple[Sequence[str], str | None]] = []

    def runner(args: Sequence[str], input_text: str | None) -> subprocess.CompletedProcess[str]:
        calls.append((list(args), input_text))
        if "list" in args:
            return subprocess.CompletedProcess(list(args), 0, stdout="[]", stderr="")
        return subprocess.CompletedProcess(
            list(args), 0, stdout="https://example.test/issues/1", stderr=""
        )

    urls = file_findings(
        [finding(), finding(summary="duplicate")],
        REPORT,
        "owner/name",
        dry_run=False,
        runner=runner,
        gh_path="/usr/local/bin/gh",
    )
    assert urls == ["https://example.test/issues/1"]
    assert len(calls) == 2
    list_args = calls[0][0]
    assert list_args[0] == "/usr/local/bin/gh"
    assert list_args[1:3] == ["issue", "list"]
    assert "--json" in list_args and "number,title,body" in list_args
    create_args, body = calls[1]
    assert create_args[1:3] == ["issue", "create"]
    assert "--label" in create_args and "stress" in create_args and "bug" in create_args
    assert "--body-file" in create_args and "-" in create_args
    assert isinstance(body, str)
    assert marker(fingerprint(finding())) in body
    assert "Labels: stress, bug" in body


def test_existing_open_issue_suppresses_creation() -> None:
    known = fingerprint(finding())
    calls: list[Sequence[str]] = []

    def runner(args: Sequence[str], input_text: str | None) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        payload = json.dumps([{"number": 9, "title": "existing", "body": f"see {marker(known)}"}])
        return subprocess.CompletedProcess(list(args), 0, stdout=payload, stderr="")

    urls = file_findings(
        [finding()],
        REPORT,
        "owner/name",
        dry_run=False,
        runner=runner,
        gh_path="/usr/local/bin/gh",
    )
    assert urls == []
    assert len(calls) == 1  # list only, no create
    assert calls[0][:3] == ["/usr/local/bin/gh", "issue", "list"]


def test_missing_gh_fails_with_a_clear_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tools.stress.issues.shutil.which", lambda name: None)
    with pytest.raises(IssueFilingError, match="gh is not installed"):
        file_findings([finding()], REPORT, "owner/name", dry_run=False)


def test_unauthenticated_gh_fails_and_keeps_the_report() -> None:
    def runner(args: Sequence[str], input_text: str | None) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            list(args), 1, stdout="", stderr="gh: To use GitHub CLI, run gh auth login"
        )

    with pytest.raises(IssueFilingError, match="gh auth"):
        file_findings(
            [finding()],
            REPORT,
            "owner/name",
            dry_run=False,
            runner=runner,
            gh_path="/usr/local/bin/gh",
        )


def test_issue_list_invalid_json_is_an_error() -> None:
    def runner(args: Sequence[str], input_text: str | None) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(args), 0, stdout="not json", stderr="")

    with pytest.raises(IssueFilingError, match="invalid JSON"):
        file_findings(
            [finding()],
            REPORT,
            "owner/name",
            dry_run=False,
            runner=runner,
            gh_path="/usr/local/bin/gh",
        )


def test_plan_preserves_first_payload_per_fingerprint() -> None:
    payloads = plan_issue_payloads([finding(), finding()], REPORT, set())
    assert len(payloads) == 1
    assert payloads[0].fingerprint == fingerprint_for(
        "UNEXPECTED_5XX", "smoke", "http.no_unexpected_5xx"
    )
