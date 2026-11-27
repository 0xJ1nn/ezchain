"""State-changing routes have their own request budget, tighter than reads.

A burst of order placement, key minting, approval decisions or backtest runs must be
bounded before a handler can create rows. The write limiter is keyed by principal,
refuses with a typed 429 that carries a retry hint, and never tracks unbounded keys.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from ezchain_api.config import Settings
from ezchain_api.rate_limit import ReadRateLimitMiddleware, WriteRateLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def _settings(**overrides: Any) -> Settings:
    return Settings(
        environment="test",
        data_mode="fixture",
        database_url="sqlite+pysqlite://",
        auto_create_schema=False,
        **overrides,
    )


class PrincipalInjector(BaseHTTPMiddleware):
    """Stand-in for bearer auth: resolves a principal from a header before the limiter."""

    async def dispatch(self, request: Any, call_next: RequestResponseEndpoint) -> Any:
        subject = request.headers.get("x-principal")
        if subject:
            request.state.principal = SimpleNamespace(subject=subject)
        return await call_next(request)


def _write_app(**limit_overrides: Any) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        ReadRateLimitMiddleware,
        settings=_settings(**limit_overrides),
    )
    # Added last so it is outermost: the principal is set before the limiter reads it.
    app.add_middleware(PrincipalInjector)

    @app.post("/api/v1/orders")
    def place_order() -> dict[str, str]:
        return {"ok": "order"}

    @app.post("/api/v1/agent/keys")
    def mint_key() -> dict[str, str]:
        return {"ok": "key"}

    @app.get("/api/v1/markets")
    def read_market() -> dict[str, str]:
        return {"ok": "read"}

    @app.post("/ops/local")
    def local_only() -> dict[str, str]:
        return {"ok": "local"}

    return app


def test_write_budget_is_configurable_and_tighter_than_read(monkeypatch: Any) -> None:
    monkeypatch.setattr("ezchain_api.rate_limit.time.monotonic", Clock().monotonic)
    defaults = _settings()
    assert defaults.write_rate_limit_per_minute > 0
    assert defaults.write_rate_limit_per_minute < defaults.read_rate_limit_per_minute

    custom = _settings(write_rate_limit_per_minute=3)
    assert custom.write_rate_limit_per_minute == 3
    limiter = WriteRateLimiter(custom)
    assert limiter.allow("principal:alice") == (True, 0)
    assert limiter.allow("principal:alice") == (True, 0)
    assert limiter.allow("principal:alice") == (True, 0)
    allowed, retry_after = limiter.allow("principal:alice")
    assert allowed is False
    assert 1 <= retry_after <= 60


def test_write_budget_is_keyed_by_principal_and_resets_with_the_window(monkeypatch: Any) -> None:
    clock = Clock()
    monkeypatch.setattr("ezchain_api.rate_limit.time.monotonic", clock.monotonic)
    limiter = WriteRateLimiter(_settings(write_rate_limit_per_minute=1))

    assert limiter.allow("principal:alice") == (True, 0)
    # bob has an independent budget; alice is refused until her window rolls over.
    assert limiter.allow("principal:bob") == (True, 0)
    assert limiter.allow("principal:alice")[0] is False
    clock.now += 61
    assert limiter.allow("principal:alice") == (True, 0)


def test_a_zero_write_budget_disables_the_limiter(monkeypatch: Any) -> None:
    monkeypatch.setattr("ezchain_api.rate_limit.time.monotonic", Clock().monotonic)
    limiter = WriteRateLimiter(_settings(write_rate_limit_per_minute=0))
    assert all(limiter.allow("principal:alice") == (True, 0) for _ in range(50))


def test_write_past_budget_is_a_typed_429_with_a_retry_hint(monkeypatch: Any) -> None:
    monkeypatch.setattr("ezchain_api.rate_limit.time.monotonic", Clock().monotonic)
    client = TestClient(_write_app(write_rate_limit_per_minute=2, read_rate_limit_per_minute=100))

    assert client.post("/api/v1/orders", headers={"x-principal": "alice"}).status_code == 200
    assert client.post("/api/v1/orders", headers={"x-principal": "alice"}).status_code == 200
    refused = client.post("/api/v1/orders", headers={"x-principal": "alice"})
    assert refused.status_code == 429
    detail = refused.json()["detail"]
    assert detail["code"] == "WRITE_RATE_LIMITED"
    assert detail["retryable"] is True
    assert 1 <= detail["retry_after_seconds"] <= 60
    assert detail["retry_after_seconds"] == int(refused.headers["Retry-After"])

    # Reads have their own budget, and writes outside /api are never limited.
    assert client.get("/api/v1/markets", headers={"x-principal": "alice"}).status_code == 200
    assert client.post("/ops/local", headers={"x-principal": "alice"}).status_code == 200


def test_a_burst_cannot_grow_the_bucket_registry_without_bound(monkeypatch: Any) -> None:
    monkeypatch.setattr("ezchain_api.rate_limit.time.monotonic", Clock().monotonic)
    limiter = WriteRateLimiter(_settings(write_rate_limit_per_minute=2), max_keys=8)
    for index in range(200):
        limiter.allow(f"principal:key-{index}")

    assert len(limiter._buckets) <= 8
    # Earlier keys were evicted, so a fresh burst from any one key is still bounded.
    allowed, retry_after = limiter.allow("principal:key-0")
    assert allowed is True
    assert limiter.allow("principal:key-0")[0] is True
    assert limiter.allow("principal:key-0")[0] is False
    assert retry_after == 0


def test_prod_refuses_a_zero_write_budget() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as raised:
        Settings(
            environment="prod",
            api_token="a" * 40,
            auth_enabled=True,
            auto_create_schema=False,
            database_url="postgresql+psycopg://ezchain:long-password@db:5432/ezchain",
            data_mode="live",
            cors_origins=["https://app.example.com"],
            write_rate_limit_per_minute=0,
        )
    assert "EZCHAIN_WRITE_RATE_LIMIT_PER_MINUTE" in str(raised.value)
