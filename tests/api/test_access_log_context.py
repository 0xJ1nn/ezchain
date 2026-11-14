"""One JSON log line per request, carrying what a money-path question needs.

The access log answered "which route, what status, how long". A money-path question is
different: which order, for which strategy, in which mode, for which request. These tests
pin the extra fields, and pin the two properties that keep the line safe and cheap — a
secret never appears in it, and a field the handler did not set is reported as absent
rather than invented.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from ezchain_api.config import Settings
from ezchain_api.observability import ACCESS_LOGGER, HttpMetrics, ObservabilityMiddleware


class ListHandler(logging.Handler):
    """Keep the emitted lines so a test can read the payload, not the text."""

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


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


def _line(handler: Any) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(handler.messages[-1])
    return payload


def _client(app: FastAPI) -> TestClient:
    return TestClient(app)


def test_a_request_without_a_money_context_logs_absent_fields_not_guesses() -> None:
    """A plain read must not carry an order id it does not have."""
    app = FastAPI()
    app.add_middleware(ObservabilityMiddleware, metrics=HttpMetrics())

    @app.get("/api/v1/ping")
    def ping() -> dict[str, str]:
        return {"ok": "true"}

    handler, old_level = capture_access_logs()
    try:
        _client(app).get("/api/v1/ping")
    finally:
        release_access_logs(handler, old_level)

    payload = _line(handler)
    assert payload["msg"] == "http_request"
    assert payload["logger"] == ACCESS_LOGGER
    # The keys exist and are null: a consumer can rely on the shape without
    # mistaking an absent order for one that happened.
    assert payload["order_id"] is None
    assert payload["strategy_id"] is None
    assert payload["intent_id"] is None


def test_the_mode_is_logged_on_every_request() -> None:
    """Mode is never absent: every number in this deployment is mode-scoped."""
    app = FastAPI()
    app.add_middleware(ObservabilityMiddleware, metrics=HttpMetrics())

    @app.get("/api/v1/ping")
    def ping() -> dict[str, str]:
        return {"ok": "true"}

    handler, old_level = capture_access_logs()
    try:
        _client(app).get("/api/v1/ping")
    finally:
        release_access_logs(handler, old_level)

    assert _line(handler)["mode"] in {"PAPER", "LIVE", "OFFLINE"}


def test_ids_a_handler_sets_appear_in_the_line() -> None:
    """The middleware reads the ids from request state, whatever the handler set."""
    app = FastAPI()
    app.add_middleware(ObservabilityMiddleware, metrics=HttpMetrics())

    @app.post("/api/v1/paper/orders")
    def order(request: Request) -> dict[str, str]:
        request.state.order_id = "ord-1"
        request.state.strategy_id = "strat-1"
        request.state.intent_id = "int-1"
        return {"status": "filled"}

    handler, old_level = capture_access_logs()
    try:
        _client(app).post("/api/v1/paper/orders")
    finally:
        release_access_logs(handler, old_level)

    payload = _line(handler)
    assert payload["order_id"] == "ord-1"
    assert payload["strategy_id"] == "strat-1"
    assert payload["intent_id"] == "int-1"


def test_a_secret_in_an_id_is_redacted() -> None:
    """An id is redacted like any other string, so a caller cannot log a token."""
    app = FastAPI()
    app.add_middleware(ObservabilityMiddleware, metrics=HttpMetrics())

    @app.post("/api/v1/paper/orders")
    def order(request: Request) -> dict[str, str]:
        request.state.order_id = "ord-secret-value-1234"
        return {"status": "filled"}

    app.state.settings = Settings(
        environment="test",
        database_url="sqlite+pysqlite://",
        api_token="ord-secret-value-1234",
    )
    handler, old_level = capture_access_logs()
    try:
        _client(app).post("/api/v1/paper/orders")
    finally:
        release_access_logs(handler, old_level)

    assert "ord-secret-value-1234" not in json.dumps(_line(handler))
