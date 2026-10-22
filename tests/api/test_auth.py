"""Bearer auth: reads open, mutations denied by default, future routes covered."""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from ezchain_api import auth
from ezchain_api.auth import READ_METHODS, BearerAuthMiddleware
from ezchain_api.config import Settings, get_settings
from ezchain_api.deps import get_session
from ezchain_api.main import create_app
from ezchain_api.routers import system

EXPECTED_PROTECTED_ROUTES = (
    ("DELETE", "/api/v1/strategies/{strategy_id}"),
    ("PATCH", "/api/v1/strategies/{strategy_id}"),
    ("POST", "/api/v1/paper/orders"),
    ("POST", "/api/v1/strategies"),
    ("POST", "/api/v1/strategies/{strategy_id}/run"),
    ("POST", "/api/v1/system/kill-switch"),
)


def make_client(settings: Settings, session: Session) -> TestClient:
    app = FastAPI()
    app.add_middleware(BearerAuthMiddleware, settings=settings)
    app.include_router(system.router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


@pytest.fixture
def protected_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"api_token": SecretStr("correct-token")})


def test_mutation_without_token_is_401(protected_settings: Settings, session: Session) -> None:
    client = make_client(protected_settings, session)

    response = client.post("/api/v1/system/kill-switch", json={"active": True})

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"
    assert response.json()["detail"]["retryable"] is False
    assert response.headers["www-authenticate"] == "Bearer"


def test_mutation_with_wrong_token_or_scheme_is_401(
    protected_settings: Settings, session: Session
) -> None:
    client = make_client(protected_settings, session)

    wrong = client.post(
        "/api/v1/system/kill-switch",
        json={"active": True},
        headers={"authorization": "Bearer not-the-token"},
    )
    basic = client.post(
        "/api/v1/system/kill-switch",
        json={"active": True},
        headers={"authorization": "Basic correct-token"},
    )

    assert wrong.status_code == 401
    assert basic.status_code == 401


@pytest.mark.asyncio
async def test_non_ascii_bearer_is_401_not_500(protected_settings: Settings) -> None:
    # httpx refuses to encode a non-ASCII header, so drive the ASGI middleware directly.
    async def inner(scope: object, receive: object, send: object) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = BearerAuthMiddleware(inner, protected_settings)
    messages: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/system/kill-switch",
        "raw_path": b"/api/v1/system/kill-switch",
        "query_string": b"",
        "headers": [(b"authorization", b"Bearer h\xe9llo")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    await middleware(scope, receive, send)  # type: ignore[arg-type]

    assert messages[0]["status"] == 401


def test_mutation_with_correct_token_succeeds(
    protected_settings: Settings, session: Session
) -> None:
    client = make_client(protected_settings, session)

    response = client.post(
        "/api/v1/system/kill-switch",
        json={"active": True, "reason": "auth drill"},
        headers={"authorization": "Bearer correct-token"},
    )

    assert response.status_code == 200
    assert response.json()["active"] is True


def test_reads_are_open(protected_settings: Settings, session: Session) -> None:
    client = make_client(protected_settings, session)

    assert client.get("/api/v1/system/kill-switch").status_code == 200
    assert client.get("/healthz").status_code == 200
    assert client.options("/api/v1/system/kill-switch").status_code != 401


def test_future_mutation_route_is_denied_by_default(
    protected_settings: Settings, session: Session
) -> None:
    client = make_client(protected_settings, session)

    denied = client.post("/api/v1/not-built-yet", json={})
    allowed = client.post(
        "/api/v1/not-built-yet",
        json={},
        headers={"authorization": "Bearer correct-token"},
    )

    assert denied.status_code == 401
    assert allowed.status_code == 404


def test_dev_without_a_token_allows_mutations_with_one_warning(
    settings: Settings,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(auth, "_warned_missing_token", False)
    no_token = settings.model_copy(update={"api_token": None, "environment": "test"})
    client = make_client(no_token, session)

    with caplog.at_level(logging.WARNING, logger="ezchain_api.auth"):
        first = client.post("/api/v1/system/kill-switch", json={"active": True})
        second = client.post("/api/v1/system/kill-switch", json={"active": True})

    assert first.status_code == 200
    assert second.status_code == 200
    warnings = [record for record in caplog.records if "EZCHAIN_API_TOKEN" in record.getMessage()]
    assert len(warnings) == 1


def test_disabling_auth_is_explicit(settings: Settings, session: Session) -> None:
    disabled = settings.model_copy(update={"auth_enabled": False, "api_token": None})
    client = make_client(disabled, session)

    assert client.post("/api/v1/system/kill-switch", json={"active": True}).status_code == 200


def _api_routes(routes: list[object]) -> list[APIRoute]:
    """Flatten FastAPI's included-router tree into concrete API routes."""
    found: list[APIRoute] = []
    for route in routes:
        if isinstance(route, APIRoute):
            found.append(route)
            continue
        original = getattr(route, "original_router", None)
        if original is not None:
            found.extend(_api_routes(original.routes))
    return found


def test_every_mutating_route_lives_under_the_auth_prefix() -> None:
    app = create_app()
    mutations = [
        (method, route.path_format)
        for route in _api_routes(list(app.routes))
        for method in route.methods or set()
        if method not in READ_METHODS
    ]
    unprotected = [(method, path) for method, path in mutations if not path.startswith("/api/")]
    assert unprotected == [], f"mutating routes outside /api/ default-deny: {unprotected}"


def test_every_mutating_api_route_is_in_the_protected_list() -> None:
    app = create_app()
    protected = sorted(
        (method, route.path_format)
        for route in _api_routes(list(app.routes))
        if route.path.startswith("/api/")
        for method in route.methods or set()
        if method not in READ_METHODS
    )
    assert tuple(protected) == EXPECTED_PROTECTED_ROUTES
