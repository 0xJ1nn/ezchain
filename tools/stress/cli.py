"""Command-line entrypoint for ``python -m tools.stress``."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import cast

from .checks import self_test
from .config import (
    DEFAULT_BASE_URL,
    DEFAULT_REPORT_DIR,
    MODES,
    STRATEGY_KINDS,
    ConfigError,
    RunConfig,
    default_duration,
)
from .issues import IssueFilingError, file_findings
from .scenarios import ExecutionResult, PreflightError, execute


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.stress",
        description=(
            "PAPER-only stress harness. Exercises markets, paper orders and strategies; "
            "verifies accounting invariants; writes evidence; optionally files one "
            "deduplicated GitHub issue per failure fingerprint."
        ),
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--mode", choices=MODES, default="smoke")
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="seconds (default: smoke/chaos 30, idempotency 60, soak 300)",
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--chain", default=None, help="chain id; default: discover")
    parser.add_argument("--token", default=None, help="token address; requires --chain")
    parser.add_argument("--token-symbol", default=None)
    parser.add_argument("--order-usd", default="1", help="paper notional per order")
    parser.add_argument("--strategy-kind", choices=STRATEGY_KINDS, default="dca")
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    parser.add_argument(
        "--file-issues",
        action="store_true",
        help="file deduplicated GitHub issues for failures (requires --repo)",
    )
    parser.add_argument("--repo", default=None, help="owner/name for issue filing")
    parser.add_argument(
        "--dry-run-issues",
        action="store_true",
        help="print issue payloads without calling gh (requires --repo)",
    )
    parser.add_argument("--json", action="store_true", help="print the report JSON to stdout")
    parser.add_argument("--self-test", action="store_true", help="run network-free checks and exit")
    parser.add_argument("--read-p95-ms", type=float, default=2000.0)
    parser.add_argument("--mutation-p95-ms", type=float, default=1500.0)
    parser.add_argument("--mutation-p99-ms", type=float, default=5000.0)
    parser.add_argument("--timeout", type=float, default=10.0)
    return parser


def _value(args: argparse.Namespace, name: str) -> object:
    return cast(object, getattr(args, name))


def _str_arg(args: argparse.Namespace, name: str) -> str:
    value = _value(args, name)
    if not isinstance(value, str):
        raise ConfigError(f"--{name.replace('_', '-')} must be a string")
    return value


def _optional_str_arg(args: argparse.Namespace, name: str) -> str | None:
    value = _value(args, name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"--{name.replace('_', '-')} must be a string")
    return value


def _int_arg(args: argparse.Namespace, name: str) -> int:
    value = _value(args, name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"--{name.replace('_', '-')} must be an integer")
    return value


def _float_arg(args: argparse.Namespace, name: str) -> float:
    value = _value(args, name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"--{name.replace('_', '-')} must be a number")
    return float(value)


def _optional_float_arg(args: argparse.Namespace, name: str) -> float | None:
    value = _value(args, name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"--{name.replace('_', '-')} must be a number")
    return float(value)


def _bool_arg(args: argparse.Namespace, name: str) -> bool:
    value = _value(args, name)
    if not isinstance(value, bool):
        raise ConfigError(f"--{name.replace('_', '-')} must be a flag")
    return value


def build_config(args: argparse.Namespace) -> RunConfig:
    mode = _str_arg(args, "mode")
    duration_override = _optional_float_arg(args, "duration")
    order_raw = _str_arg(args, "order_usd")
    try:
        order_usd = Decimal(order_raw)
    except (InvalidOperation, ValueError) as exc:
        raise ConfigError("--order-usd must be a decimal") from exc
    return RunConfig(
        base_url=_str_arg(args, "base_url"),
        mode=mode,
        duration=duration_override if duration_override is not None else default_duration(mode),
        concurrency=_int_arg(args, "concurrency"),
        seed=_int_arg(args, "seed"),
        chain=_optional_str_arg(args, "chain"),
        token=_optional_str_arg(args, "token"),
        token_symbol=_optional_str_arg(args, "token_symbol"),
        order_usd=order_usd,
        strategy_kind=_str_arg(args, "strategy_kind"),
        report_dir=Path(_str_arg(args, "report_dir")),
        file_issues=_bool_arg(args, "file_issues"),
        repo=_optional_str_arg(args, "repo"),
        dry_run_issues=_bool_arg(args, "dry_run_issues"),
        json_output=_bool_arg(args, "json"),
        read_p95_ms=_float_arg(args, "read_p95_ms"),
        mutation_p95_ms=_float_arg(args, "mutation_p95_ms"),
        mutation_p99_ms=_float_arg(args, "mutation_p99_ms"),
        timeout_s=_float_arg(args, "timeout"),
    )


def _summary(result: ExecutionResult) -> None:
    report = result.report
    status = report.get("status")
    findings = len(result.findings)
    print(
        f"stress: {str(status).upper()} — {findings} unique finding(s) — {result.json_path}",
        file=sys.stderr,
    )
    print(f"stress: markdown {result.markdown_path}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if _bool_arg(args, "self_test"):
        self_test()
        print("self-test: ok")
        return 0

    try:
        config = build_config(args)
    except ConfigError as exc:
        print(f"CONFIG_ERROR: {exc}", file=sys.stderr)
        return 2

    raw_argv = list(sys.argv[1:] if argv is None else argv)
    command = shlex.join(["python", "-m", "tools.stress", *raw_argv])
    try:
        result = execute(config, command=command)
    except PreflightError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"RUN_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    _summary(result)
    if config.json_output:
        print(json.dumps(result.report, indent=2, sort_keys=True, default=str))

    if config.file_issues or config.dry_run_issues:
        if not result.findings:
            print("no issues filed: run passed", file=sys.stderr)
        else:
            try:
                file_findings(
                    result.findings,
                    result.report,
                    config.repo,
                    dry_run=config.dry_run_issues,
                    out=sys.stderr if config.json_output else sys.stdout,
                )
            except IssueFilingError as exc:
                print(f"ISSUE_FILING_FAILED: {exc}", file=sys.stderr)
                return 3

    return 0 if result.report.get("status") == "pass" else 1
