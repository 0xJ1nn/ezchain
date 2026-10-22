"""HTTP client, metrics and harness state for the PAPER stress run.

Only read endpoints and paper-mutation endpoints are reachable through
:meth:`Harness.request`. The chaos scenario is the single, explicit exception: it targets a
deliberately invalid path/URL to exercise retry/backoff, and is documented as such.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, cast
from urllib.parse import urlsplit

import httpx

from .checks import (
    ACCOUNT_INVARIANTS,
    HTTP_INVARIANTS,
    LATENCY_INVARIANTS,
    ORDER_INVARIANTS,
    POSITION_INVARIANTS,
    RECONCILIATION_INVARIANTS,
    Finding,
    check_account,
    check_http,
    check_latency,
    check_orders_and_fills,
    check_positions,
    check_reconciliation,
    percentiles,
)
from .config import ConfigError, RunConfig
from .issues import fingerprint_for

Params = Mapping[str, str | int | float]

ALLOWED_PATH_PREFIXES = (
    "/health",
    "/api/v1/chains",
    "/api/v1/markets",
    "/api/v1/tokens",
    "/api/v1/quotes",
    "/api/v1/paper",
    "/api/v1/strategies",
    # The operator kill switch. Scoped to the exact path: the rest of /api/v1/system is
    # not something a load harness has any business touching.
    "/api/v1/system/kill-switch",
)
_STRATEGY_PATH_RE = re.compile(r"(/api/v1/strategies/)[^/]+")
_TOKEN_PATH_RE = re.compile(r"(/api/v1/tokens/[^/]+/)[^/]+")
_GROUP_ORDER = ("reads", "paper_mutations", "chaos")

# One retry only, and only for GET: a reset pooled connection is noise, a down API is not.
_TRANSPORT_ATTEMPTS = 2
_TRANSPORT_RETRY_DELAY_S = 0.05

# How a request authenticates. ``default`` uses EZCHAIN_STRESS_API_TOKEN when set,
# ``omit`` sends no Authorization header and ``wrong`` sends a deliberately invalid one.
AuthMode = Literal["default", "omit", "wrong"]


def is_allowed_path(path: str) -> bool:
    if not path.startswith("/") or "://" in path or ".." in path:
        return False
    return any(path == prefix or path.startswith(prefix + "/") for prefix in ALLOWED_PATH_PREFIXES)


def endpoint_label(method: str, path: str) -> str:
    clean = path.split("?", 1)[0]
    clean = _STRATEGY_PATH_RE.sub(r"\1{id}", clean)
    clean = _TOKEN_PATH_RE.sub(r"\1{address}", clean)
    return f"{method.upper()} {clean}"


def endpoint_group(method: str, path: str) -> str:
    upper = method.upper()
    if upper in {"POST", "PATCH", "DELETE"} and (
        path.startswith("/api/v1/paper") or path.startswith("/api/v1/strategies")
    ):
        return "paper_mutations"
    if path.startswith("/health") or path.startswith("/api/v1"):
        return "reads"
    return "chaos"


@dataclass(frozen=True)
class RequestRecord:
    method: str
    endpoint: str
    group: str
    status: int | None
    latency_ms: float
    error: str | None
    expected_error: bool
    scenario: str


class Metrics:
    """Thread-safe request counters and latency samples."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: list[RequestRecord] = []

    def record(self, record: RequestRecord) -> None:
        with self._lock:
            self._records.append(record)

    def records(self) -> list[RequestRecord]:
        with self._lock:
            return list(self._records)

    def statuses(self) -> list[int]:
        return [record.status for record in self.records() if record.status is not None]

    def unexpected_transport_errors(self) -> list[str]:
        return [
            record.error or "unknown"
            for record in self.records()
            if record.status is None and record.error and not record.expected_error
        ]

    def latency_samples(self) -> dict[str, list[float]]:
        samples: dict[str, list[float]] = {group: [] for group in _GROUP_ORDER}
        for record in self.records():
            if record.status is not None:
                samples.setdefault(record.group, []).append(record.latency_ms)
        return samples

    def snapshot(self) -> dict[str, object]:
        records = self.records()
        by_status: dict[str, int] = {}
        by_group: dict[str, dict[str, object]] = {}
        for record in records:
            status_key = "transport_error" if record.status is None else str(record.status)
            by_status[status_key] = by_status.get(status_key, 0) + 1
            group = by_group.setdefault(record.group, {"total": 0, "by_status": {}})
            group["total"] = cast(int, group["total"]) + 1
            group_statuses = cast(dict[str, int], group["by_status"])
            group_statuses[status_key] = group_statuses.get(status_key, 0) + 1
        unexpected = [r.error for r in records if r.status is None and not r.expected_error]
        expected = [r.error for r in records if r.status is None and r.expected_error]
        return {
            "total": len(records),
            "by_status": dict(sorted(by_status.items())),
            "by_group": {key: by_group[key] for key in sorted(by_group)},
            "transport_errors": {
                "unexpected": len(unexpected),
                "expected": len(expected),
                "expected_samples": sorted(set(error or "unknown" for error in expected)),
            },
            "unexpected_5xx": sum(1 for status in self.statuses() if 500 <= status <= 599),
        }

    def latency_snapshot(self) -> dict[str, object]:
        samples = self.latency_samples()
        return {
            group: percentiles(samples.get(group, ()))
            for group in sorted(set(_GROUP_ORDER) | set(samples))
        }


class _ThreadClients(threading.local):
    clients: dict[str, httpx.Client]

    def __init__(self) -> None:
        self.clients = {}


class Harness:
    """Shared run state plus a per-thread HTTP client."""

    max_orders = 180
    # The matrix walks every paper chain and needs its own room: it runs last in
    # ``--mode all`` and must not be starved by the load modes that filled the shared cap.
    max_matrix_orders = 60
    # The race needs a handful of orders of its own for the same reason.
    max_race_orders = 16

    def __init__(self, config: RunConfig) -> None:
        self.config = config
        self.metrics = Metrics()
        self._lock = threading.Lock()
        self._findings: list[Finding] = []
        self._finding_counts: dict[str, int] = {}
        self._expected_invariants: set[str] = set()
        self._skipped_invariants: set[str] = set()
        self._skip_details: dict[str, str] = {}
        self._created_order_ids: list[str] = []
        self._order_summaries: list[dict[str, object]] = []
        self._created_strategy_ids: list[str] = []
        self._deleted_strategy_ids: list[str] = []
        self._notes: list[str] = []
        self._sections: dict[str, list[dict[str, object]]] = {}
        self._order_slots = 0
        self._matrix_slots = 0
        self._race_slots = 0
        self._sequence = 0
        self._transport_retries = 0
        self._paper_state_broken: str | None = None
        self._threads = _ThreadClients()
        self._api_token = os.environ.get("EZCHAIN_STRESS_API_TOKEN") or None
        if self._api_token is not None:
            parsed = urlsplit(config.base_url)
            loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            if parsed.scheme != "https" and not loopback:
                raise ConfigError(
                    "EZCHAIN_STRESS_API_TOKEN must not be sent over plain HTTP to a "
                    "non-loopback host"
                )
        self.run_tag = f"{config.seed}-{int(time.time())}"
        self.started_monotonic = time.monotonic()

    # -- lifecycle -----------------------------------------------------

    @property
    def api_token_configured(self) -> bool:
        """True when a bearer token came from ``EZCHAIN_STRESS_API_TOKEN``."""
        return self._api_token is not None

    def _authorization(self, auth: AuthMode) -> str | None:
        if auth == "omit":
            return None
        if auth == "wrong":
            # Not a credential: a fixed, obviously invalid value.
            return "Bearer stress-wrong-token-0000000000000000"
        if self._api_token is None:
            return None
        return f"Bearer {self._api_token}"

    def _client(self, origin: str, auth: AuthMode = "default") -> httpx.Client:
        key = f"{auth}|{origin}"
        client = self._threads.clients.get(key)
        if client is None:
            timeout = httpx.Timeout(self.config.timeout_s, connect=min(3.0, self.config.timeout_s))
            authorization = self._authorization(auth)
            client = httpx.Client(
                timeout=timeout,
                follow_redirects=False,
                headers={
                    "User-Agent": "ezchain-stress/0.2 (paper-only)",
                    "Accept": "application/json",
                    **({"Authorization": authorization} if authorization is not None else {}),
                },
            )
            self._threads.clients[key] = client
        return client

    def close_thread_clients(self) -> None:
        for client in self._threads.clients.values():
            client.close()
        self._threads.clients.clear()

    def _drop_client(self, origin: str, auth: AuthMode) -> None:
        """Discard one pooled client so the retry dials a fresh connection."""
        client = self._threads.clients.pop(f"{auth}|{origin}", None)
        if client is not None:
            client.close()

    def _note_transport_retry(self, path: str, error: str) -> None:
        with self._lock:
            self._transport_retries += 1
            if self._transport_retries <= 5:
                self._notes.append(f"retried a reset connection on GET {path} ({error})")

    @property
    def transport_retries(self) -> int:
        with self._lock:
            return self._transport_retries

    def mark_paper_state_broken(self, reason: str) -> None:
        """Remember that the paper account can no longer be read consistently.

        An oversell leaves a negative position, and every later read replays it back as a
        5xx. Remaining scenarios are skipped with this reason instead of adding a hundred
        cascade findings on top of the one that matters.
        """
        with self._lock:
            if self._paper_state_broken is None:
                self._paper_state_broken = reason

    @property
    def paper_state_broken(self) -> str | None:
        with self._lock:
            return self._paper_state_broken

    def redact(self, text: str) -> str:
        """Never let the deployment token reach a report or issue payload."""
        if self._api_token is None:
            return text
        return text.replace(self._api_token, "[redacted]")

    def next_id(self, prefix: str) -> str:
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        return f"stress-{prefix}-{self.run_tag}-{sequence}"

    def reserve_order_slot(self) -> bool:
        with self._lock:
            if self._order_slots >= self.max_orders:
                return False
            self._order_slots += 1
            return True

    def reserve_matrix_slot(self) -> bool:
        with self._lock:
            if self._matrix_slots >= self.max_matrix_orders:
                return False
            self._matrix_slots += 1
            return True

    def reserve_race_slot(self) -> bool:
        with self._lock:
            if self._race_slots >= self.max_race_orders:
                return False
            self._race_slots += 1
            return True

    def note_order(self, order_id: str) -> None:
        with self._lock:
            if order_id not in self._created_order_ids:
                self._created_order_ids.append(order_id)

    def note_order_summary(self, order: Mapping[str, object]) -> None:
        summary = {
            key: order.get(key)
            for key in ("id", "side", "status", "reason", "amount_usd", "quote_kind")
            if key in order
        }
        with self._lock:
            if len(self._order_summaries) < 200:
                self._order_summaries.append(dict(summary))

    @property
    def order_summaries(self) -> list[dict[str, object]]:
        with self._lock:
            return list(self._order_summaries)

    def note_strategy(self, strategy_id: str) -> None:
        with self._lock:
            if strategy_id not in self._created_strategy_ids:
                self._created_strategy_ids.append(strategy_id)

    def note_strategy_deleted(self, strategy_id: str) -> None:
        with self._lock:
            if strategy_id not in self._deleted_strategy_ids:
                self._deleted_strategy_ids.append(strategy_id)

    @property
    def created_order_ids(self) -> set[str]:
        with self._lock:
            return set(self._created_order_ids)

    @property
    def created_strategy_ids(self) -> list[str]:
        with self._lock:
            return list(self._created_strategy_ids)

    @property
    def deleted_strategy_ids(self) -> set[str]:
        with self._lock:
            return set(self._deleted_strategy_ids)

    def note(self, message: str) -> None:
        """Record a non-failure skip so the report does not look silently incomplete."""
        with self._lock:
            if len(self._notes) < 100:
                self._notes.append(message)

    @property
    def notes(self) -> list[str]:
        with self._lock:
            return list(self._notes)

    def record_row(self, section: str, row: Mapping[str, object]) -> None:
        """Append one evidence row (matrix/fuzz/auth/race) to a named report section."""
        with self._lock:
            rows = self._sections.setdefault(section, [])
            if len(rows) < 200:
                rows.append(dict(row))

    @property
    def sections(self) -> dict[str, list[dict[str, object]]]:
        with self._lock:
            return {name: [dict(row) for row in rows] for name, rows in self._sections.items()}

    # -- findings and invariants --------------------------------------

    def add_finding(self, finding: Finding) -> None:
        key = fingerprint_for(finding.code, finding.scenario, finding.invariant)
        with self._lock:
            if key in self._finding_counts:
                self._finding_counts[key] += 1
                return
            if len(self._findings) < 500:
                self._findings.append(finding)
            self._finding_counts[key] = 1

    def add_findings(self, findings: Sequence[Finding]) -> None:
        for finding in findings:
            self.add_finding(finding)

    @property
    def findings(self) -> list[Finding]:
        with self._lock:
            return list(self._findings)

    def finding_occurrences(self, finding: Finding) -> int:
        key = fingerprint_for(finding.code, finding.scenario, finding.invariant)
        with self._lock:
            return self._finding_counts.get(key, 1)

    def register_invariants(self, *identifiers: str) -> None:
        with self._lock:
            self._expected_invariants.update(identifiers)

    def skip_invariant(self, identifier: str, detail: str | None = None) -> None:
        with self._lock:
            self._expected_invariants.add(identifier)
            self._skipped_invariants.add(identifier)
            if detail is not None:
                self._skip_details[identifier] = detail

    def unskip_invariant(self, identifier: str) -> None:
        with self._lock:
            self._skipped_invariants.discard(identifier)
            self._skip_details.pop(identifier, None)

    def invariant_report(self) -> list[dict[str, object]]:
        findings = self.findings
        failed: dict[str, int] = {}
        for finding in findings:
            failed[finding.invariant] = failed.get(finding.invariant, 0) + self.finding_occurrences(
                finding
            )
        with self._lock:
            expected = set(self._expected_invariants)
            skipped = set(self._skipped_invariants)
            skip_details = dict(self._skip_details)
        identifiers = sorted(expected | set(failed))
        rows: list[dict[str, object]] = []
        for identifier in identifiers:
            if identifier in failed:
                status = "fail"
                detail = f"{failed[identifier]} occurrence(s)"
            elif identifier in skipped:
                status = "skip"
                detail = skip_details.get(identifier, "endpoint absent or not applicable")
            else:
                status = "pass"
                detail = "ok"
            rows.append({"id": identifier, "status": status, "detail": detail})
        return rows

    # -- HTTP ----------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        scenario: str,
        params: Params | None = None,
        json_body: Mapping[str, object] | None = None,
        content: str | bytes | None = None,
        content_type: str | None = None,
        expected: Sequence[int] = (200,),
        allow_status: Sequence[int] = (),
        allow_error: bool = False,
        allow_5xx: bool = False,
        origin: str | None = None,
        timeout_s: float | None = None,
        path_ok: bool = False,
        auth: AuthMode = "default",
    ) -> httpx.Response | None:
        """Perform one request, recording latency/status and unexpected failures.

        ``path_ok`` and ``allow_error`` exist only for the chaos scenario. They are never
        wired to a CLI flag. ``content`` sends a raw body (used by the fuzz matrix for
        bodies that are not valid JSON); ``auth`` selects the bearer token behaviour.
        """
        if not path_ok and not is_allowed_path(path):
            raise ValueError(f"refusing non-allowlisted path: {path!r}")
        if not path.startswith("/") or "://" in path:
            raise ValueError(f"path must be origin-relative: {path!r}")
        target_origin = (origin or self.config.origin).rstrip("/")
        url = f"{target_origin}{path}"
        started = time.perf_counter()
        headers = {"Content-Type": content_type} if content_type is not None else None
        # A keep-alive socket reset by the server is not proof that the API is down, so an
        # idempotent read is retried exactly once on a fresh connection. A real outage
        # fails both attempts and is reported as before.
        attempts = _TRANSPORT_ATTEMPTS if method.upper() == "GET" else 1
        response: httpx.Response | None = None
        transport_error: httpx.HTTPError | None = None
        for attempt in range(attempts):
            client = self._client(target_origin, auth)
            try:
                if timeout_s is None:
                    response = client.request(
                        method,
                        url,
                        params=params,
                        json=json_body,
                        content=content,
                        headers=headers,
                    )
                else:
                    response = client.request(
                        method,
                        url,
                        params=params,
                        json=json_body,
                        content=content,
                        headers=headers,
                        timeout=timeout_s,
                    )
                break
            except httpx.HTTPError as exc:
                transport_error = exc
                if attempt + 1 < attempts:
                    self._drop_client(target_origin, auth)
                    self._note_transport_retry(path, type(exc).__name__)
                    time.sleep(_TRANSPORT_RETRY_DELAY_S)

        if response is None:
            assert transport_error is not None
            latency = (time.perf_counter() - started) * 1000
            error = type(transport_error).__name__
            self.metrics.record(
                RequestRecord(
                    method=method.upper(),
                    endpoint=endpoint_label(method, path),
                    group=endpoint_group(method, path),
                    status=None,
                    latency_ms=round(latency, 3),
                    error=error,
                    expected_error=allow_error,
                    scenario=scenario,
                )
            )
            if not allow_error:
                self.add_finding(
                    Finding(
                        code="CONNECTION_ERROR",
                        scenario=scenario,
                        invariant="http.no_connection_errors",
                        summary="a request could not reach the API",
                        expected=f"{method.upper()} {path} succeeds",
                        actual=error,
                        evidence=self.redact(str(transport_error)[:500]),
                    )
                )
            return None

        latency = (time.perf_counter() - started) * 1000
        status = response.status_code
        self.metrics.record(
            RequestRecord(
                method=method.upper(),
                endpoint=endpoint_label(method, path),
                group=endpoint_group(method, path),
                status=status,
                latency_ms=round(latency, 3),
                error=None,
                expected_error=False,
                scenario=scenario,
            )
        )
        if status >= 500 and not allow_5xx:
            self.add_finding(
                Finding(
                    code="UNEXPECTED_5XX",
                    scenario=scenario,
                    invariant="http.no_unexpected_5xx",
                    summary=f"{method.upper()} {path} returned {status}",
                    expected="no 5xx responses",
                    actual=f"HTTP {status}",
                    evidence=self.redact(response.text[:500]),
                )
            )
        if status not in expected and status not in allow_status:
            self.add_finding(
                Finding(
                    code="UNEXPECTED_STATUS",
                    scenario=scenario,
                    invariant="http.unexpected_status",
                    summary=f"{method.upper()} {path} returned an unexpected status",
                    expected=f"one of {sorted(set(expected) | set(allow_status))}",
                    actual=f"HTTP {status}",
                    evidence=self.redact(response.text[:500]),
                )
            )
        return response

    def json_object(
        self,
        response: httpx.Response | None,
        *,
        scenario: str,
        label: str,
    ) -> dict[str, object] | None:
        if response is None:
            return None
        try:
            payload: object = response.json()
        except (ValueError, UnicodeDecodeError):
            self.add_finding(
                Finding(
                    code="INVALID_JSON",
                    scenario=scenario,
                    invariant="http.valid_json",
                    summary=f"{label} did not return JSON",
                    expected="a JSON object",
                    actual=response.text[:200],
                )
            )
            return None
        if not isinstance(payload, Mapping):
            self.add_finding(
                Finding(
                    code="INVALID_JSON",
                    scenario=scenario,
                    invariant="http.valid_json",
                    summary=f"{label} did not return a JSON object",
                    expected="a JSON object",
                    actual=repr(payload)[:200],
                )
            )
            return None
        return cast(dict[str, object], payload)

    def json_list(
        self,
        response: httpx.Response | None,
        *,
        scenario: str,
        label: str,
    ) -> list[dict[str, object]] | None:
        if response is None:
            return None
        try:
            payload: object = response.json()
        except (ValueError, UnicodeDecodeError):
            self.add_finding(
                Finding(
                    code="INVALID_JSON",
                    scenario=scenario,
                    invariant="http.valid_json",
                    summary=f"{label} did not return JSON",
                    expected="a JSON list",
                    actual=response.text[:200],
                )
            )
            return None
        if not isinstance(payload, list):
            self.add_finding(
                Finding(
                    code="INVALID_JSON",
                    scenario=scenario,
                    invariant="http.valid_json",
                    summary=f"{label} did not return a JSON list",
                    expected="a JSON list",
                    actual=repr(payload)[:200],
                )
            )
            return None
        rows: list[dict[str, object]] = []
        for item in cast(Sequence[object], payload):
            if isinstance(item, Mapping):
                rows.append(cast(dict[str, object], item))
            else:
                self.add_finding(
                    Finding(
                        code="INVALID_JSON",
                        scenario=scenario,
                        invariant="http.valid_json",
                        summary=f"{label} list contains a non-object row",
                        expected="objects",
                        actual=repr(item)[:200],
                    )
                )
        return rows

    # -- composite checks ---------------------------------------------

    def check_paper_snapshot(self, *, scenario: str) -> None:
        """Read paper state and apply the order/fill, position and account checks."""
        orders_response = self.request(
            "GET", "/api/v1/paper/orders", scenario=scenario, params={"limit": 200}
        )
        orders = self.json_list(orders_response, scenario=scenario, label="paper orders") or []
        fills_response = self.request(
            "GET", "/api/v1/paper/fills", scenario=scenario, params={"limit": 200}
        )
        fills = self.json_list(fills_response, scenario=scenario, label="paper fills") or []
        positions_response = self.request("GET", "/api/v1/paper/positions", scenario=scenario)
        positions = (
            self.json_list(positions_response, scenario=scenario, label="paper positions") or []
        )
        account_response = self.request("GET", "/api/v1/paper/account", scenario=scenario)
        account = self.json_object(account_response, scenario=scenario, label="paper account")

        order_findings = check_orders_and_fills(
            orders,
            fills,
            scenario=scenario,
            order_ids=self.created_order_ids,
        )
        position_findings = check_positions(positions, scenario=scenario)
        account_findings = check_account(account, scenario=scenario) if account is not None else []
        self.add_findings(order_findings)
        self.add_findings(position_findings)
        self.add_findings(account_findings)
        self.register_invariants(*ORDER_INVARIANTS, *POSITION_INVARIANTS, *ACCOUNT_INVARIANTS)

        reconciliation = self.request(
            "GET",
            "/api/v1/paper/reconciliation",
            scenario=scenario,
            expected=(200,),
            allow_status=(404,),
        )
        reconciliation_status = None if reconciliation is None else reconciliation.status_code
        reconciliation_payload = (
            self.json_object(reconciliation, scenario=scenario, label="reconciliation")
            if reconciliation_status == 200
            else None
        )
        self.add_findings(
            check_reconciliation(
                reconciliation_status,
                reconciliation_payload,
                scenario=scenario,
            )
        )
        self.register_invariants(*RECONCILIATION_INVARIANTS)

    def finish_checks(self, *, scenario: str) -> None:
        """HTTP and latency checks over the whole run."""
        self.add_findings(
            check_http(
                self.metrics.statuses(),
                self.metrics.unexpected_transport_errors(),
                scenario=scenario,
                chaos=self.config.mode == "chaos",
            )
        )
        self.register_invariants(*HTTP_INVARIANTS)
        self.add_findings(
            check_latency(
                self.metrics.latency_samples(),
                scenario=scenario,
                read_p95_ms=self.config.read_p95_ms,
                mutation_p95_ms=self.config.mutation_p95_ms,
                mutation_p99_ms=self.config.mutation_p99_ms,
            )
        )
        self.register_invariants(*LATENCY_INVARIANTS)
