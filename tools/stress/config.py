"""Typed run configuration and the safety preconditions it enforces."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

MODES = ("smoke", "soak", "idempotency", "chaos")
STRATEGY_KINDS = ("momentum", "dip", "dca", "threshold")
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_REPORT_DIR = Path("tools/stress/reports")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class ConfigError(ValueError):
    """The command line would be unsafe or cannot be run."""


def default_duration(mode: str) -> float:
    if mode == "soak":
        return 300.0
    if mode == "idempotency":
        return 60.0
    return 30.0


@dataclass(frozen=True)
class RunConfig:
    base_url: str = DEFAULT_BASE_URL
    mode: str = "smoke"
    duration: float = 30.0
    concurrency: int = 4
    seed: int = 0
    chain: str | None = None
    token: str | None = None
    token_symbol: str | None = None
    order_usd: Decimal = Decimal("1")
    strategy_kind: str = "dca"
    report_dir: Path = DEFAULT_REPORT_DIR
    file_issues: bool = False
    repo: str | None = None
    dry_run_issues: bool = False
    json_output: bool = False
    read_p95_ms: float = 2000.0
    mutation_p95_ms: float = 1500.0
    mutation_p99_ms: float = 5000.0
    timeout_s: float = 10.0

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ConfigError(f"unknown --mode {self.mode!r}; choose one of {', '.join(MODES)}")
        if not isinstance(self.duration, (int, float)) or not math.isfinite(self.duration):
            raise ConfigError("--duration must be a finite number of seconds")
        if self.duration <= 0:
            raise ConfigError("--duration must be > 0")
        if self.concurrency < 1:
            raise ConfigError("--concurrency must be >= 1")
        if self.strategy_kind not in STRATEGY_KINDS:
            raise ConfigError(
                f"unknown --strategy-kind {self.strategy_kind!r}; "
                f"choose one of {', '.join(STRATEGY_KINDS)}"
            )
        if not isinstance(self.order_usd, Decimal):
            try:
                object.__setattr__(self, "order_usd", Decimal(str(self.order_usd)))
            except (InvalidOperation, ValueError) as exc:
                raise ConfigError("--order-usd must be a decimal") from exc
        if not self.order_usd.is_finite() or self.order_usd <= 0:
            raise ConfigError("--order-usd must be a positive finite decimal")
        if self.token is not None:
            object.__setattr__(self, "token", self.token.strip() or None)
        if self.chain is not None:
            object.__setattr__(self, "chain", self.chain.strip() or None)
        if self.token is not None and self.chain is None:
            raise ConfigError("--token requires --chain; token addresses are not unique")
        if self.token is not None and len(self.token) < 8:
            raise ConfigError("--token must be a full token address (>= 8 characters)")
        if self.repo is not None and not _REPO_RE.match(self.repo):
            raise ConfigError("--repo must look like owner/name")
        if (self.file_issues or self.dry_run_issues) and not self.repo:
            raise ConfigError("--file-issues/--dry-run-issues require --repo owner/name")
        if self.read_p95_ms <= 0 or self.mutation_p95_ms <= 0 or self.mutation_p99_ms <= 0:
            raise ConfigError("latency budgets must be > 0")
        if self.timeout_s <= 0:
            raise ConfigError("--timeout must be > 0")

        raw = self.base_url.strip()
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ConfigError("--base-url must be an absolute http(s) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ConfigError("credentials in --base-url are refused; the harness sends no auth")
        if parsed.path not in {"", "/"}:
            raise ConfigError("--base-url must be an origin; do not include a path")
        if parsed.query or parsed.fragment:
            raise ConfigError("--base-url must not include a query or fragment")
        normalized = f"{parsed.scheme}://{parsed.netloc}"
        object.__setattr__(self, "base_url", normalized.rstrip("/"))
        object.__setattr__(self, "report_dir", Path(self.report_dir).expanduser())

    @property
    def origin(self) -> str:
        return self.base_url

    @property
    def token_symbol_or_default(self) -> str:
        return self.token_symbol or "STRESS"
