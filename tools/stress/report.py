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

SCHEMA_VERSION = 2
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
        "strategy_order_usd": str(config.strategy_order_usd),
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
        "sections": harness.sections,
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
            "auth_headers_sent": harness.api_token_configured,
            "auth_token_source": "EZCHAIN_STRESS_API_TOKEN (redacted from every artifact)",
            "signing_or_keys": False,
            "transport_retries": harness.transport_retries,
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
                "/api/v1/paper/reconciliation",
                "/api/v1/strategies",
                "/api/v1/system/kill-switch",
            ],
            "mutation_paths": [
                "POST /api/v1/paper/orders",
                "POST /api/v1/strategies",
                "PATCH /api/v1/strategies/{id}",
                "DELETE /api/v1/strategies/{id}",
                "POST /api/v1/strategies/{id}/run",
                "POST /api/v1/system/kill-switch (killswitch mode; previous state restored)",
            ],
            "limits": [
                "the harness never kills processes, restarts services or injects server faults",
                "chaos uses only a deliberately invalid path and an unreachable loopback port",
                "no live venue, signing operation or key material is ever involved",
                "order/fill invariants use the newest 200 rows and the order ids this run created",
                "the kill switch is restored to its previous state in a finally block",
                "fuzz cases are invalid or malformed payloads; they never reach a live venue",
            ],
        },
    }
    return report


def _section_rows(report: Mapping[str, object], name: str) -> list[Mapping[str, object]]:
    sections = _as_mapping(report.get("sections"))
    rows: list[Mapping[str, object]] = []
    for item in _as_sequence(sections.get(name)):
        mapping = _as_mapping(item)
        if mapping:
            rows.append(mapping)
    return rows


def _render_ledger(lines: list[str], report: Mapping[str, object]) -> None:
    rows = _section_rows(report, "ledger")
    if not rows:
        return
    ledger = rows[0]
    lines.extend(["", "## Ledger recompute (independent of the API)", ""])
    lines.append(f"- {_as_str(ledger.get('detail'), 'no fills observed')}")
    lines.append(
        f"- Cash before/after: {_as_str(ledger.get('cash_before'), '-')} → "
        f"{_as_str(ledger.get('cash_after'), '-')}"
    )
    skip = ledger.get("skip")
    if skip is not None:
        lines.append(f"- **Skipped:** `{_as_str(skip)}`")
    tokens = _as_sequence(ledger.get("tokens"))
    if not tokens:
        lines.append("")
        lines.append("No token was traded, so there is no position delta to prove.")
        return
    lines.extend(
        [
            "",
            "| chain:token | buys | sells | expected Δqty | observed Δqty | ok |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for item in tokens:
        row = _as_mapping(item)
        label = f"{_as_str(row.get('chain'))}:{_as_str(row.get('token_address'))}"
        buys = _as_str(row.get("buy_quantity"), "0")
        sells = _as_str(row.get("sell_quantity"), "0")
        expected = _as_str(row.get("expected_delta"), "0")
        observed = _as_str(row.get("observed_delta"), "0")
        ok = "yes" if row.get("ok") is True else "**no**"
        lines.append(f"| {label} | {buys} | {sells} | {expected} | {observed} | {ok} |")


def _render_matrix(lines: list[str], report: Mapping[str, object]) -> None:
    rows = _section_rows(report, "matrix")
    if not rows:
        return
    lines.extend(
        [
            "",
            "## Chain matrix",
            "",
            "| chain | token | quote_kind | buy | sell | status | reason |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for row in rows:
        token = _as_str(row.get("symbol") or row.get("token")) or "-"
        quote_kind = _as_str(row.get("quote_kind")) or "unknown"
        buy = _as_str(row.get("buy")) or "-"
        sell = _as_str(row.get("sell")) or "-"
        status = _as_str(row.get("status")) or "-"
        reason = _as_str(row.get("reason")) or "-"
        chain = _as_str(row.get("chain"))
        lines.append(f"| {chain} | {token} | {quote_kind} | {buy} | {sell} | {status} | {reason} |")
    statuses: dict[str, int] = {}
    for row in rows:
        key = _as_str(row.get("status"), "unknown")
        statuses[key] = statuses.get(key, 0) + 1
    summary = ", ".join(f"{key}={count}" for key, count in sorted(statuses.items()))
    lines.append("")
    lines.append(f"Totals: {summary}")


def _render_fuzz(lines: list[str], report: Mapping[str, object]) -> None:
    rows = _section_rows(report, "fuzz")
    if not rows:
        return
    failed = sum(1 for row in rows if row.get("ok") is not True)
    lines.extend(
        [
            "",
            "## Fuzz matrix",
            "",
            f"{len(rows)} case(s), {failed} not as expected.",
            "",
            "| case | expected | status | ok | detail |",
            "|---|---|---:|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            f"| `{_as_str(row.get('case_id'))}` | {_as_str(row.get('expected'))} | "
            f"{_as_str(row.get('status'), '-')} | {'yes' if row.get('ok') is True else '**no**'} | "
            f"{_as_str(row.get('detail'), '')} |"
        )


def _render_auth(lines: list[str], report: Mapping[str, object]) -> None:
    rows = _section_rows(report, "auth")
    if not rows:
        return
    lines.extend(
        [
            "",
            "## Auth negatives",
            "",
            "| case | status | ok | detail |",
            "|---|---:|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            f"| {_as_str(row.get('case'))} | {_as_str(row.get('status'), '-')} | "
            f"{'yes' if row.get('ok') is True else '**no**'} | {_as_str(row.get('detail'), '')} |"
        )


def _render_race_and_killswitch(lines: list[str], report: Mapping[str, object]) -> None:
    race = _section_rows(report, "race")
    if race:
        row = race[0]
        lines.extend(
            [
                "",
                "## Oversell race",
                "",
                f"- Held: {_as_str(row.get('held_quantity'), '-')} · sold: "
                f"{_as_str(row.get('sold_quantity'), '-')} · final: "
                f"{_as_str(row.get('final_quantity'), 'gone')}",
                f"- Concurrent full-size sells: {_as_str(row.get('concurrent_sells'), '-')} "
                f"({_as_str(row.get('filled_sell_orders'), '0')} filled)",
                f"- Cash after: {_as_str(row.get('cash_usd'), '-')}",
            ]
        )
    killswitch = _section_rows(report, "killswitch")
    if killswitch:
        lines.extend(["", "## Kill switch", "", "| step | detail |", "|---|---|"])
        for row in killswitch:
            step = _as_str(row.get("step"))
            if step == "blocked":
                detail = (
                    f"action={_as_str(row.get('action'), '-')} "
                    f"reason={_as_str(row.get('reason'), '-')} "
                    f"orders_created={_as_str(row.get('orders_created'), '0')}"
                )
            elif step == "manual_order":
                detail = (
                    f"HTTP {_as_str(row.get('status_code'), '-')} "
                    f"status={_as_str(row.get('status'), '-')} "
                    f"reason={_as_str(row.get('reason'), '-')}"
                )
            else:
                detail = (
                    f"previous active={_as_str(row.get('previous_active'))} "
                    f"reason={_as_str(row.get('previous_reason'), '-')}"
                )
            lines.append(f"| {step} | {detail} |")


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

    _render_ledger(lines, report)
    _render_matrix(lines, report)
    _render_fuzz(lines, report)
    _render_auth(lines, report)
    _render_race_and_killswitch(lines, report)

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
        lines.extend(["None. Every check passed.", ""])
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
