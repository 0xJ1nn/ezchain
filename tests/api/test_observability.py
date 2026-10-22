"""Structured logs, request ids and the bounded Prometheus surface."""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from ezchain_api.config import Settings
from ezchain_api.deps import get_session
from ezchain_api.models import PaperOrderRow
from ezchain_api.observability import (
    ACCESS_LOGGER,
    HttpMetrics,
    ObservabilityMiddleware,
    redact,
    render_prometheus,
    safe_request_id,
)
from ezchain_api.routers import system


class ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def observed_client(metrics: HttpMetrics) -> TestClient:
    app = FastAPI()
    app.add_middleware(ObservabilityMiddleware, metrics=metrics)

    @app.get("/api/v1/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/v1/ping")
    def post_ping() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app)


def capture_access_logs() -> tuple[ListHandler, int]:
    handler = ListHandler()
    logger = logging.getLogger(ACCESS_LOGGER)
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return handler, old_level


def release_access_logs(handler: ListHandler, old_level: int) -> None:
    logger = logging.getLogger(ACCESS_LOGGER)
    logger.removeHandler(handler)
    logger.setLevel(old_level)


def test_safe_request_id_accepts_only_short_log_safe_values() -> None:
    assert safe_request_id("abc-123_XY") == "abc-123_XY"
    assert safe_request_id("a" * 64) == "a" * 64
    assert safe_request_id("a" * 65) is None
    assert safe_request_id("bad id") is None
    assert safe_request_id("bad/id") is None
    assert safe_request_id(None) is None


def test_request_id_is_echoed_or_generated() -> None:
    client = observed_client(HttpMetrics())

    echoed = client.get("/api/v1/ping", headers={"x-request-id": "trace-42"})
    generated = client.get("/api/v1/ping", headers={"x-request-id": "bad id"})

    assert echoed.headers["x-request-id"] == "trace-42"
    assert generated.headers["x-request-id"] != "bad id"
    assert len(generated.headers["x-request-id"]) == 36


def test_access_log_is_one_json_line_without_secrets() -> None:
    handler, old_level = capture_access_logs()
    try:
        client = observed_client(HttpMetrics())
        response = client.post(
            "/api/v1/ping?api_token=query-secret",
            json={"password": "body-secret"},
            headers={
                "authorization": "Bearer header-secret",
                "x-request-id": "req-1",
            },
        )
    finally:
        release_access_logs(handler, old_level)

    assert response.status_code == 200
    line = handler.messages[-1]
    payload: dict[str, Any] = json.loads(line)
    assert payload["msg"] == "http_request"
    assert payload["logger"] == ACCESS_LOGGER
    assert payload["request_id"] == "req-1"
    assert payload["method"] == "POST"
    assert payload["route"] == "/api/v1/ping"
    assert payload["status"] == 200
    assert isinstance(payload["duration_ms"], float)
    assert payload["ts"].endswith("+00:00")
    for secret in ("header-secret", "body-secret", "query-secret"):
        assert secret not in line


def test_redact_removes_bearer_credentials() -> None:
    text = "failed Authorization: Bearer abc.def-123 with known-secret"

    redacted = redact(text, ["known-secret"])

    assert "[redacted]" in redacted
    assert "abc.def-123" not in redacted
    assert "known-secret" not in redacted


def test_metrics_have_the_required_shape() -> None:
    metrics = HttpMetrics()
    client = observed_client(metrics)
    assert client.get("/api/v1/ping").status_code == 200
    assert client.get("/api/v1/ping").status_code == 200

    text_body = render_prometheus(
        metrics, up=True, paper_orders={"pending": 1, "filled": 2, "rejected": 3}, paper_fills=2
    )

    assert (
        'ezchain_http_requests_total{method="GET",route="/api/v1/ping",status="200"} 2' in text_body
    )
    assert 'ezchain_http_request_duration_ms_bucket{method="GET",route="/api/v1/ping",le="5"}' in (
        text_body
    )
    assert (
        'ezchain_http_request_duration_ms_bucket{method="GET",route="/api/v1/ping",le="+Inf"}'
        in (text_body)
    )
    assert (
        'ezchain_http_request_duration_ms_count{method="GET",route="/api/v1/ping"} 2' in text_body
    )
    assert 'ezchain_paper_orders_total{status="filled"} 2' in text_body
    assert "ezchain_paper_fills_total 2" in text_body
    assert "ezchain_up 1" in text_body


def test_metric_series_are_capped_and_dropped_samples_are_counted() -> None:
    metrics = HttpMetrics(max_series=2)
    for index in range(5):
        metrics.observe("GET", f"/api/v1/route-{index}", 200, 1.0)

    text_body = render_prometheus(metrics, up=True, paper_orders={}, paper_fills=0)

    request_lines = [
        line for line in text_body.splitlines() if line.startswith("ezchain_http_requests_total{")
    ]
    assert len(request_lines) == 2
    dropped = next(
        line for line in text_body.splitlines() if line.startswith("ezchain_metrics_dropped_total ")
    )
    assert int(dropped.split()[-1]) > 0


def test_unknown_methods_are_folded_into_one_label() -> None:
    metrics = HttpMetrics()
    metrics.observe("BREW", "/api/v1/ping", 200, 1.0)

    text_body = render_prometheus(metrics, up=True, paper_orders={}, paper_fills=0)

    assert 'method="OTHER"' in text_body
    assert "BREW" not in text_body


def _stamp(session: Session, revision: str) -> None:
    session.execute(
        text(
            "CREATE TABLE IF NOT EXISTS alembic_version "
            "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        )
    )
    session.execute(text("DELETE FROM alembic_version"))
    session.execute(
        text("INSERT INTO alembic_version (version_num) VALUES (:revision)"), {"revision": revision}
    )
    session.commit()


def system_client(session: Session) -> TestClient:
    app = FastAPI()
    app.include_router(system.router)
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


def test_healthz_never_touches_the_database() -> None:
    app = FastAPI()
    app.include_router(system.router)

    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_requires_the_migration_head(session: Session) -> None:
    _stamp(session, "0001_baseline")
    client = system_client(session)

    ready = client.get("/readyz")

    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["revision"] == "0001_baseline"
    assert ready.json()["migration"] == "head"


def test_readyz_is_503_when_the_revision_is_pending(session: Session) -> None:
    _stamp(session, "0000_old")
    client = system_client(session)

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["migration"] == "pending"


def test_readyz_is_503_when_the_database_is_unstamped(session: Session) -> None:
    response = system_client(session).get("/readyz")

    assert response.status_code == 503
    assert response.json()["migration"] == "missing"


def test_readyz_and_health_are_503_when_the_database_is_down() -> None:
    class BrokenSession:
        def execute(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("database down")

        def rollback(self) -> None:
            return None

    app = FastAPI()
    app.include_router(system.router)
    app.dependency_overrides[get_session] = lambda: BrokenSession()
    client = TestClient(app)

    ready = client.get("/readyz")
    health = client.get("/health")
    metrics = client.get("/metrics")

    assert ready.status_code == 503
    assert ready.json()["db"] == "down"
    assert health.status_code == 503
    assert metrics.status_code == 200
    assert "ezchain_up 0" in metrics.text


def test_health_keeps_the_compatibility_shape(session: Session) -> None:
    app = FastAPI()
    app.include_router(system.router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[system.get_settings] = lambda: Settings(environment="test")

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["db"] == "ok"
    assert body["engine_inline"] is True
    assert body["market_scan_inline"] is True
    assert body["version"] == "0.1.0"


def test_metrics_endpoint_reports_paper_counts(session: Session) -> None:
    _stamp(session, "0001_baseline")
    session.add(
        PaperOrderRow(
            idempotency_key="metrics-filled-order",
            chain="solana",
            token_address="token-address",
            side="buy",
            amount_usd=Decimal("10"),
            status="filled",
        )
    )
    session.commit()

    response = system_client(session).get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert 'ezchain_paper_orders_total{status="filled"} 1' in response.text
    assert "ezchain_paper_fills_total 0" in response.text
    assert "ezchain_up 1" in response.text
