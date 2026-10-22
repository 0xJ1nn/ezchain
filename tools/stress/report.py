"""JSON and Markdown evidence for a stress run.

The report is the artifact a reviewer reads first. It records what ran, what it touched,
what it measured and what failed. Request headers are never captured; the redaction helper
exists so future additions cannot accidentally leak one.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from .checks import Finding
from .config import RunConfig
from .http import Harness
from .issues import fingerprint

SCHEMA_VERSION = 1
MAX_FAILURE_SAMPLES = 5


def _as_str(value: object, default: str = "") -> str:
    if value is None:
        return default
    return value if isinstance(value, str) else str(value)


def _as_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def _as_sequence(value: object) -> Sequence[object]:
    if isinstance(value, list):
        return cast(Sequence[object], value)
    return []


def _git_info() -> tuple[str, bool]:
    root = Path(__file__).resolve().parents[2]
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown", True
    sha = commit.stdout.strip() if commit.returncode == 0 else "unknown"
    dirty = bool(status.stdout.strip()) if status.returncode == 0 else True
    return sha or "unknown", dirty


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Redact credential-shaped headers. The harness sends none, but reports may grow."""
    redacted: dict[str, str] = {}
    sensitive = ("authorization", "cookie", "token", "secret", "api-key", "apikey", "key")
    for key, value in headers.items():
        if any(word in key.lower() for word in sensitive):
            redacted[key] = "<redacted>"
        else:
            redacted[key] = value
    return redacted


def _finding_sample(finding: Finding, occurrences: int) -> dict[str, object]:
    return {
        "code": finding.code,
        "scenario": finding.scenario,
        "invariant": finding.invariant,
        "fingerprint": fingerprint(finding),
        "summary": finding.summary,
        "expected": finding.expected,
        "actual": finding.actual,
        "evidence": finding.evidence[:1000],
        "occurrences": occurrences,
        "headers": redact_headers({}),
    }


def build_report(
    *,
    config: RunConfig,
    harness: Harness,
    token_info: Mapping[str, object] | None,
    command: str,
    started_at: datetime,
    finished_at: datetime,
    error: str | None = None,
) -> dict[str, object]:
    findings = sorted(harness.findings, key=fingerprint)
    commit, dirty = _git_info()
    samples = [
        _finding_sample(finding, harness.finding_occurrences(finding))
        for finding in findings[:MAX_FAILURE_SAMPLES]
    ]
    token_chain = _as_str(token_info.get("chain")) if token_info else config.chain
    token_address = _as_str(token_info.get("address")) if token_info else config.token
    token_symbol = _as_str(token_info.get("symbol")) if token_info else config.token_symbol
    report: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "status": "fail" if findings or error else "pass",
        "mode": config.mode,
        "seed": config.seed,
        "duration_s": config.duration,
        "elapsed_s": round((finished_at - started_at).total_seconds(), 3),
        "concurrency": config.concurrency,
        "base_url": config.base_url,
        "chain": token_chain,
        "token_address": token_address,
        "token_symbol": token_symbol,
        "order_usd": str(config.order_usd),
        "strategy_kind": config.strategy_kind,
        "command": command,
        "git_commit": commit,
        "git_dirty": dirty,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "report_path": "",
        "requests": harness.metrics.snapshot(),
        "latency_ms": harness.metrics.latency_snapshot(),
        "latency_budgets_ms": {
            "reads_p95": config.read_p95_ms,
            "paper_mutations_p95": config.mutation_p95_ms,
            "paper_mutations_p99": config.mutation_p99_ms,
        },
        "invariants": harness.invariant_report(),
        "findings_total": len(findings),
        "findings_occurrences": sum(harness.finding_occurrences(f) for f in findings),
        "failure_samples": samples,
        "created_ids": {
            "orders": sorted(harness.created_order_ids),
            "strategies": harness.created_strategy_ids,
            "deleted_strategies": sorted(harness.deleted_strategy_ids),
        },
        "order_summaries": harness.order_summaries,
        "skips": harness.notes,
        "error": error,
        "safety": {
            "paper_mode_verified": True,
            "auth_headers_sent": False,
            "signing_or_keys": False,
            "read_paths": [
                "/health",
                "/api/v1/chains",
                "/api/v1/markets",
                "/api/v1/markets/overview (optional)",
                "/api/v1/tokens/{chain}/{address}",
                "/api/v1/quotes",
                "/api/v1/paper/account",
                "/api/v1/paper/positions",
                "/api/v1/paper/orders",
                "/api/v1/paper/fills",
                "/api/v1/paper/reconciliation (optional)",
                "/api/v1/strategies",
            ],
            "mutation_paths": [
                "POST /api/v1/paper/orders",
                "POST /api/v1/strategies",
                "PATCH /api/v1/strategies/{id}",
                "DELETE /api/v1/strategies/{id}",
                "POST /api/v1/strategies/{id}/run",
            ],
            "limits": [
                "the harness never kills processes, restarts services or injects server faults",
                "chaos uses only a deliberately invalid path and an unreachable loopback port",
                "no live venue, signing operation or key material is ever involved",
                "order/fill invariants use the newest 200 rows and the order ids this run created",
            ],
        },
    }
    return report


def render_markdown(report: Mapping[str, object]) -> str:
    requests = _as_mapping(report.get("requests"))
    latency = _as_mapping(report.get("latency_ms"))
    by_status = _as_mapping(requests.get("by_status"))
    by_group = _as_mapping(requests.get("by_group"))
    invariants = _as_sequence(report.get("invariants"))
    samples = _as_sequence(report.get("failure_samples"))
    created = _as_mapping(report.get("created_ids"))
    lines: list[str] = [
        f"# PAPER stress report — {_as_str(report.get('mode'), 'unknown')}",
        "",
        f"- **Result:** {_as_str(report.get('status'), 'unknown').upper()}",
        f"- **Started:** {_as_str(report.get('started_at'))}",
        f"- **Elapsed:** {_as_str(report.get('elapsed_s'))} s",
        f"- **Seed:** {_as_str(report.get('seed'))}",
        f"- **Concurrency:** {_as_str(report.get('concurrency'))}",
        f"- **Base URL:** {_as_str(report.get('base_url'))}",
        f"- **Chain / token:** {_as_str(report.get('chain'), '-')} / "
        f"{_as_str(report.get('token_address'), '-')}",
        f"- **Git commit:** {_as_str(report.get('git_commit'))}"
        + (" (dirty)" if report.get("git_dirty") is True else ""),
        f"- **Command:** `{_as_str(report.get('command'))}`",
        f"- **JSON:** `{_as_str(report.get('report_path'))}`",
        "",
        "All results are **PAPER**. No keys, signing or live venue were involved.",
        "",
        "## Requests",
        "",
        f"Total: {_as_str(requests.get('total'), '0')} · unexpected 5xx: "
        f"{_as_str(requests.get('unexpected_5xx'), '0')}",
        "",
        "| status | count |",
        "|---|---:|",
    ]
    for status in sorted(by_status):
        lines.append(f"| {status} | {_as_str(by_status.get(status))} |")
    lines.extend(["", "| group | total |", "|---|---:|"])
    for group in sorted(by_group):
        lines.append(f"| {group} | {_as_str(_as_mapping(by_group.get(group)).get('total'), '0')} |")

    lines.extend(
        [
            "",
            "## Latency (ms)",
            "",
            "| group | n | p50 | p95 | p99 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for group in sorted(latency):
        stats = _as_mapping(latency.get(group))
        lines.append(
            f"| {group} | {_as_str(stats.get('count'), '0')} | {_as_str(stats.get('p50'))} | "
            f"{_as_str(stats.get('p95'))} | {_as_str(stats.get('p99'))} |"
        )
    budgets = _as_mapping(report.get("latency_budgets_ms"))
    lines.extend(
        [
            "",
            f"Budgets: reads p95 ≤ {_as_str(budgets.get('reads_p95'))} ms, "
            f"paper mutations p95 ≤ {_as_str(budgets.get('paper_mutations_p95'))} ms, "
            f"p99 ≤ {_as_str(budgets.get('paper_mutations_p99'))} ms.",
            "",
            "## Invariant checks",
            "",
            "| invariant | status | detail |",
            "|---|---|---|",
        ]
    )
    for row in invariants:
        item = _as_mapping(row)
        lines.append(
            f"| `{_as_str(item.get('id'))}` | {_as_str(item.get('status'))} | "
            f"{_as_str(item.get('detail'))} |"
        )

    lines.extend(
        [
            "",
            "## Findings",
            "",
            f"Unique fingerprints: {_as_str(report.get('findings_total'), '0')} · "
            f"occurrences: {_as_str(report.get('findings_occurrences'), '0')}",
            "",
        ]
    )
    if not samples:
        lines.append("None. Every check passed.")
    for sample in samples:
        item = _as_mapping(sample)
        lines.extend(
            [
                f"### `{_as_str(item.get('code'))}` — {_as_str(item.get('summary'))}",
                "",
                f"- Fingerprint: `{_as_str(item.get('fingerprint'))}`",
                f"- Invariant: `{_as_str(item.get('invariant'))}`",
                f"- Occurrences: {_as_str(item.get('occurrences'))}",
                f"- Expected: {_as_str(item.get('expected'))}",
                f"- Actual: {_as_str(item.get('actual'))}",
            ]
        )
        evidence = _as_str(item.get("evidence"))
        if evidence:
            lines.extend(["", "```text", evidence, "```"])
        lines.append("")

    strategy_ids = (
        ", ".join(_as_str(value) for value in _as_sequence(created.get("strategies"))) or "none"
    )
    deleted_ids = (
        ", ".join(_as_str(value) for value in _as_sequence(created.get("deleted_strategies")))
        or "none"
    )
    lines.extend(
        [
            "## Created IDs",
            "",
            f"- Orders: {len(_as_sequence(created.get('orders')))}",
            f"- Strategies: {strategy_ids}",
            f"- Deleted strategies: {deleted_ids}",
            "",
            "## Skips",
            "",
        ]
    )
    skips = _as_sequence(report.get("skips"))
    if skips:
        for skip in skips:
            lines.append(f"- {_as_str(skip)}")
    else:
        lines.append("None.")
    lines.extend(
        [
            "",
            "## Safety properties",
            "",
        ]
    )
    safety = _as_mapping(report.get("safety"))
    for limit in _as_sequence(safety.get("limits")):
        lines.append(f"- {_as_str(limit)}")
    lines.append("")
    return "\n".join(lines)


def write_reports(report: dict[str, object], report_dir: Path) -> tuple[Path, Path]:
    """Write JSON and Markdown; returns ``(json_path, markdown_path)``."""
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    stem = (
        f"stress-{_as_str(report.get('mode'), 'run')}-{stamp}-"
        f"seed{_as_str(report.get('seed'), '0')}-{os.getpid()}"
    )
    json_path = report_dir / f"{stem}.json"
    markdown_path = report_dir / f"{stem}.md"
    report["report_path"] = str(json_path)
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path
