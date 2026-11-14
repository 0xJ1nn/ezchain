"""Structured request logs, Prometheus metrics and health helpers.

No third-party observability dependency: one JSON line per request and a small,
thread-safe in-process registry rendered as Prometheus text. Labels are bounded by the
route template and a hard series cap; raw paths, query strings, request bodies and
``Authorization`` headers never reach the log.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import ASGIApp

ACCESS_LOGGER = "ezchain_api.access"
UNMATCHED_ROUTE = "<unmatched>"
MAX_SERIES = 500
DURATION_BUCKETS_MS = (5.0, 10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1000.0, 2500.0, 5000.0)
SAFE_METHODS = frozenset({"GET", "POST", "PATCH", "DELETE", "PUT", "HEAD", "OPTIONS"})

_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")

logger = logging.getLogger(ACCESS_LOGGER)
_logging_configured = False


def configure_logging(level: str) -> None:
    """Make the access logger emit bare JSON lines without touching other loggers."""
    global _logging_configured
    numeric = getattr(logging, level.upper(), logging.INFO)
    if not isinstance(numeric, int):
        numeric = logging.INFO
    logger.setLevel(numeric)
    logger.propagate = False
    if _logging_configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._ezchain_json = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    _logging_configured = True


def safe_request_id(value: str | None) -> str | None:
    """Accept only a short, log-safe caller request id."""
    if value is None:
        return None
    return value if _REQUEST_ID.fullmatch(value) else None


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """Remove bearer credentials and known secrets from a log-bound string."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return _BEARER.sub(r"\1[redacted]", text)


def normalize_method(method: str) -> str:
    upper = method.upper()
    return upper if upper in SAFE_METHODS else "OTHER"


def route_template(request: Request) -> str:
    """The matched route pattern, never the concrete path (bounded and safe)."""
    route = request.scope.get("route")
    template = getattr(route, "path_format", None)
    return str(template) if template else UNMATCHED_ROUTE


@dataclass
class _Duration:
    buckets: list[int] = field(default_factory=lambda: [0] * (len(DURATION_BUCKETS_MS) + 1))
    total_ms: float = 0.0
    count: int = 0


class ReadRateLimited:
    """How many read requests the limiter has refused.

    The limiter runs as middleware, before routing, so it cannot use ``HttpMetrics``: a
    refused request never reaches the handler that observes a route. A process-lifetime
    counter on its own series is enough to answer "is this deployment being throttled".
    """

    def __init__(self) -> None:
        self.value = 0
        self._lock = threading.Lock()

    def increment(self) -> None:
        with self._lock:
            self.value += 1

    def reset(self) -> None:
        with self._lock:
            self.value = 0


READ_RATE_LIMITED = ReadRateLimited()


class HttpMetrics:
    """A bounded Prometheus registry for HTTP counters and a latency histogram."""

    def __init__(self, max_series: int = MAX_SERIES) -> None:
        self._max_series = max_series
        self._requests: dict[tuple[str, str, str], int] = {}
        self._durations: dict[tuple[str, str], _Duration] = {}
        self._dropped = 0
        self._lock = threading.Lock()

    def observe(self, method: str, route: str, status: int, duration_ms: float) -> None:
        method_key = normalize_method(method)
        request_key = (method_key, route, str(status))
        duration_key = (method_key, route)
        with self._lock:
            if request_key in self._requests:
                self._requests[request_key] += 1
            elif len(self._requests) < self._max_series:
                self._requests[request_key] = 1
            else:
                self._dropped += 1

            duration = self._durations.get(duration_key)
            if duration is None:
                if len(self._durations) >= self._max_series:
                    self._dropped += 1
                    return
                duration = _Duration()
                self._durations[duration_key] = duration
            duration.count += 1
            duration.total_ms += max(0.0, duration_ms)
            for index, boundary in enumerate(DURATION_BUCKETS_MS):
                if duration_ms <= boundary:
                    duration.buckets[index] += 1
            duration.buckets[-1] += 1

    def render(self) -> list[str]:
        lines = [
            "# HELP ezchain_http_requests_total HTTP requests by method, route and status.",
            "# TYPE ezchain_http_requests_total counter",
        ]
        with self._lock:
            requests = sorted(self._requests.items())
            durations = sorted(self._durations.items())
            dropped = self._dropped
        for (method, route, status), count in requests:
            lines.append(
                "ezchain_http_requests_total"
                f'{{method="{_label(method)}",route="{_label(route)}",'
                f'status="{_label(status)}"}} {count}'
            )

        lines.extend(
            [
                "# HELP ezchain_http_request_duration_ms HTTP request duration in milliseconds.",
                "# TYPE ezchain_http_request_duration_ms histogram",
            ]
        )
        for (method, route), duration in durations:
            labels = f'method="{method}",route="{_label(route)}"'
            for boundary, count in zip(DURATION_BUCKETS_MS, duration.buckets, strict=False):
                lines.append(
                    "ezchain_http_request_duration_ms_bucket"
                    f'{{{labels},le="{_number(boundary)}"}} {count}'
                )
            lines.append(
                f'ezchain_http_request_duration_ms_bucket{{{labels},le="+Inf"}} '
                f"{duration.buckets[-1]}"
            )
            lines.append(
                f"ezchain_http_request_duration_ms_sum{{{labels}}} {duration.total_ms:.3f}"
            )
            lines.append(f"ezchain_http_request_duration_ms_count{{{labels}}} {duration.count}")

        lines.extend(
            [
                "# HELP ezchain_metrics_dropped_total Metric samples dropped by the series cap.",
                "# TYPE ezchain_metrics_dropped_total counter",
                f"ezchain_metrics_dropped_total {dropped}",
            ]
        )
        return lines


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _number(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)


HTTP_METRICS = HttpMetrics()

# The alert rules read a numeric gauge, so each breaker state maps to one value. Zero is
# closed, which is why ``ezchain_breaker_state > 0`` means "degraded or refusing calls".
BREAKER_STATE_VALUES: Final[dict[str, int]] = {"closed": 0, "half_open": 1, "open": 2}


@dataclass(frozen=True)
class SourceFreshnessSample:
    """One source class's newest observation, as the process can prove it."""

    source: str
    source_class: str
    age_seconds: float | None


@dataclass(frozen=True)
class OpsSnapshot:
    """Operational state sampled from persisted and in-process state.

    Built once per scrape by :func:`ezchain_api.services.ops_signals.build_ops_snapshot`.
    An empty snapshot is not rendered: the caller omits the families when the database
    cannot be read, so a broken process reports absence instead of a confident zero.
    """

    source_freshness: tuple[SourceFreshnessSample, ...] = ()
    freshness_budgets: Mapping[str, float] = field(default_factory=dict)
    breakers: Mapping[str, str] = field(default_factory=dict)
    queues: Mapping[str, int] = field(default_factory=dict)
    queue_capacities: Mapping[str, int] = field(default_factory=dict)
    ledger_drift_entries: int = 0


def render_ops(snapshot: OpsSnapshot) -> list[str]:
    """The four operational families the alert rules read.

    Every value here is sampled state: an age measured against the clock, a breaker's
    actual state, a queue's actual depth, a reconciliation check's actual result. An age
    the process cannot establish is ``NaN`` (unknown is not zero); a breaker state outside
    the known vocabulary is omitted rather than guessed.
    """
    lines = [
        "# HELP ezchain_source_freshness_seconds Age of the newest observation per source.",
        "# TYPE ezchain_source_freshness_seconds gauge",
    ]
    for sample in sorted(
        snapshot.source_freshness, key=lambda item: (item.source_class, item.source)
    ):
        age = "NaN" if sample.age_seconds is None else f"{max(0.0, sample.age_seconds):.3f}"
        lines.append(
            f'ezchain_source_freshness_seconds{{source="{_label(sample.source)}",'
            f'source_class="{_label(sample.source_class)}"}} {age}'
        )

    lines.extend(
        [
            "# HELP ezchain_source_freshness_budget_seconds Configured freshness budget "
            "per source class.",
            "# TYPE ezchain_source_freshness_budget_seconds gauge",
        ]
    )
    for source_class in sorted(snapshot.freshness_budgets):
        lines.append(
            "ezchain_source_freshness_budget_seconds"
            f'{{source_class="{_label(source_class)}"}} '
            f"{_number(float(snapshot.freshness_budgets[source_class]))}"
        )

    lines.extend(
        [
            "# HELP ezchain_breaker_state Circuit breaker state per provider "
            "(0 closed, 1 half-open, 2 open).",
            "# TYPE ezchain_breaker_state gauge",
        ]
    )
    for provider in sorted(snapshot.breakers):
        state = snapshot.breakers[provider]
        value = BREAKER_STATE_VALUES.get(state)
        if value is None:
            continue
        lines.append(
            f'ezchain_breaker_state{{provider="{_label(provider)}",'
            f'state="{_label(state)}"}} {value}'
        )

    lines.extend(
        [
            "# HELP ezchain_queue_depth Rows or callers waiting in a bounded queue.",
            "# TYPE ezchain_queue_depth gauge",
        ]
    )
    for queue in sorted(snapshot.queues):
        lines.append(
            f'ezchain_queue_depth{{queue="{_label(queue)}"}} {int(snapshot.queues[queue])}'
        )

    lines.extend(
        [
            "# HELP ezchain_queue_capacity Configured bound of a bounded queue, so an "
            "alert threshold is the queue's own limit.",
            "# TYPE ezchain_queue_capacity gauge",
        ]
    )
    for queue in sorted(snapshot.queue_capacities):
        lines.append(
            f'ezchain_queue_capacity{{queue="{_label(queue)}"}} '
            f"{int(snapshot.queue_capacities[queue])}"
        )

    lines.extend(
        [
            "# HELP ezchain_ledger_drift_entries Failing reconciliation checks.",
            "# TYPE ezchain_ledger_drift_entries gauge",
            f"ezchain_ledger_drift_entries {int(snapshot.ledger_drift_entries)}",
        ]
    )
    return lines


def render_prometheus(
    metrics: HttpMetrics,
    *,
    up: bool,
    paper_orders: Mapping[str, int],
    paper_fills: int,
    ops: OpsSnapshot | None = None,
) -> str:
    lines = metrics.render()
    lines.extend(
        [
            "# HELP ezchain_up 1 when the API can reach its database, 0 otherwise.",
            "# TYPE ezchain_up gauge",
            f"ezchain_up {1 if up else 0}",
            "# HELP ezchain_paper_orders_total Persisted paper orders by status.",
            "# TYPE ezchain_paper_orders_total counter",
        ]
    )
    for status in sorted(paper_orders):
        lines.append(
            f'ezchain_paper_orders_total{{status="{_label(status)}"}} {paper_orders[status]}'
        )
    lines.extend(
        [
            "# HELP ezchain_paper_fills_total Persisted paper fills.",
            "# TYPE ezchain_paper_fills_total counter",
            f"ezchain_paper_fills_total {paper_fills}",
            "# HELP ezchain_read_rate_limited_total Read requests refused by the read limiter.",
            "# TYPE ezchain_read_rate_limited_total counter",
            f"ezchain_read_rate_limited_total {READ_RATE_LIMITED.value}",
        ]
    )
    if ops is not None:
        lines.extend(render_ops(ops))
    return "\n".join(lines) + "\n"


def access_log(
    *,
    request_id: str,
    method: str,
    route: str,
    status: int,
    duration_ms: float,
    secrets: Iterable[str] = (),
) -> dict[str, Any]:
    """Build the record that becomes one JSON line. Headers and bodies are excluded."""
    payload: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(),
        "level": "INFO",
        "logger": ACCESS_LOGGER,
        "msg": "http_request",
        "request_id": request_id,
        "method": normalize_method(method),
        "route": redact(route, secrets),
        "status": status,
        "duration_ms": round(max(0.0, duration_ms), 3),
    }
    return payload


class ObservabilityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, metrics: HttpMetrics | None = None) -> None:
        super().__init__(app)
        self._metrics = metrics if metrics is not None else HTTP_METRICS

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        supplied = safe_request_id(request.headers.get("x-request-id"))
        request_id = supplied or str(uuid4())
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            self._record(request, request_id, 500, started)
            raise
        self._record(request, request_id, response.status_code, started)
        response.headers["X-Request-ID"] = request_id
        return response

    def _record(self, request: Request, request_id: str, status: int, started: float) -> None:
        duration_ms = (time.perf_counter() - started) * 1000
        route = route_template(request)
        self._metrics.observe(request.method, route, status, duration_ms)
        if request.url.path == "/metrics":
            # Prometheus scrapes are noisy and already visible in the metrics themselves.
            return
        logger.info(
            json.dumps(
                access_log(
                    request_id=request_id,
                    method=request.method,
                    route=route,
                    status=status,
                    duration_ms=duration_ms,
                )
            )
        )
