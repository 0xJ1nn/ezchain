"""The unauthenticated read surface is limited, and quote sizes are capped before a call.

Two acceptance criteria that had code but no proof: a read limiter that answers 429 with a
Retry-After instead of silently letting a client exhaust the shared provider quota, and a
quote route that refuses an oversized notional before any provider is contacted.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ezchain_adapters import QuoteRouter
from ezchain_api.config import Settings
from ezchain_api.deps import get_quote_router
from ezchain_api.observability import READ_RATE_LIMITED, HttpMetrics, render_prometheus
from ezchain_api.rate_limit import ReadRateLimiter, ReadRateLimitMiddleware
from ezchain_api.routers import markets as markets_router
from tests.api.test_quotes import FakeDex, FakeJupiter


class Clock:
    """A monotonic clock the test advances, so a window boundary is provable, not waited out."""

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


def test_the_limiter_refuses_past_its_window_limit_and_reports_a_retry(monkeypatch) -> None:
    clock = Clock()
    monkeypatch.setattr("ezchain_api.rate_limit.time.monotonic", clock.monotonic)
    limiter = ReadRateLimiter(_settings(read_rate_limit_per_minute=2))

    assert limiter.allow("peer:203.0.113.9") == (True, 0)
    assert limiter.allow("peer:203.0.113.9") == (True, 0)
    allowed, retry_after = limiter.allow("peer:203.0.113.9")
    assert allowed is False
    assert 1 <= retry_after <= 60

    # One peer's budget is not another's, and a new window restores the first.
    assert limiter.allow("peer:198.51.100.4") == (True, 0)
    clock.now += 61
    assert limiter.allow("peer:203.0.113.9") == (True, 0)


def test_a_zero_limit_disables_the_limiter(monkeypatch) -> None:
    monkeypatch.setattr("ezchain_api.rate_limit.time.monotonic", Clock().monotonic)
    limiter = ReadRateLimiter(_settings(read_rate_limit_per_minute=0))
    assert all(limiter.allow("peer:203.0.113.9") == (True, 0) for _ in range(50))


def _read_app(limit: int) -> FastAPI:
    """A minimal app with the real middleware, so the response shape is the production one."""

    app = FastAPI()
    app.add_middleware(ReadRateLimitMiddleware, settings=_settings(read_rate_limit_per_minute=limit))

    @app.get("/api/probe")
    def probe() -> dict[str, str]:
        return {"ok": "read"}

    @app.post("/api/probe")
    def write_probe() -> dict[str, str]:
        return {"ok": "write"}

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"ok": "health"}

    return app


def test_a_read_past_the_limit_is_429_with_retry_after_and_counted(monkeypatch) -> None:
    monkeypatch.setattr("ezchain_api.rate_limit.time.monotonic", Clock().monotonic)
    client = TestClient(_read_app(limit=2))
    before = READ_RATE_LIMITED.value

    assert client.get("/api/probe").status_code == 200
    assert client.get("/api/probe").status_code == 200
    refused = client.get("/api/probe")
    assert refused.status_code == 429
    assert 1 <= int(refused.headers["Retry-After"]) <= 60
    assert refused.json()["detail"]["code"] == "RATE_LIMITED"

    # A write is not a read, and a probe outside /api is never limited.
    assert client.post("/api/probe").status_code == 200
    assert client.get("/healthz").status_code == 200
    assert READ_RATE_LIMITED.value == before + 1


def test_the_metric_series_is_rendered() -> None:
    rendered = render_prometheus(HttpMetrics(), up=True, paper_orders={}, paper_fills=0)
    assert "ezchain_read_rate_limited_total" in rendered


def test_an_oversized_quote_is_refused_before_any_provider_call(
    client_for: Callable[..., TestClient],
    session: Session,
) -> None:
    jupiter = FakeJupiter()
    router = QuoteRouter(providers=[jupiter], dexscreener=FakeDex())  # type: ignore[arg-type]
    client = client_for(markets_router.router, {get_quote_router: lambda: router})

    response = client.get(
        "/api/v1/quotes",
        params={"chain": "solana", "token": "mint", "side": "buy", "amount_usd": "1000001"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "QUOTE_NOTIONAL_TOO_LARGE"
    assert jupiter.calls == []
