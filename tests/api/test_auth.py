"""Bearer auth: reads open, mutations denied by default, future routes covered."""

from __future__ import annotations

import logging
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy.orm import Session

from ezchain_api import auth
from ezchain_api.auth import (
    AUTHENTICATED_READ_PATHS,
    LOOPBACK_ONLY_PATHS,
    OPERATIONAL_PATHS,
    READ_METHODS,
    BearerAuthMiddleware,
    required_scope,
    requires_auth,
)
from ezchain_api.config import Settings, get_settings
from ezchain_api.deps import get_session
from ezchain_api.main import create_app
from ezchain_api.models import PaperOrderRow
from ezchain_api.routers import system

REMOTE_PEER = ("203.0.113.9", 50000)
LOCAL_PEER = ("127.0.0.1", 50000)
METRICS_CONTRACT = (
    "# HELP ezchain_up",
    "ezchain_up ",
    "# HELP ezchain_http_requests_total",
    "# HELP ezchain_http_request_duration_ms",
    "# HELP ezchain_paper_orders_total",
    "# HELP ezchain_paper_fills_total",
)

EXPECTED_PROTECTED_ROUTES = (
    ("DELETE", "/api/v1/agent/keys/{key_id}"),
    ("DELETE", "/api/v1/provider-keys/{key_id}"),
    ("DELETE", "/api/v1/strategies/{strategy_id}"),
    ("PATCH", "/api/v1/protection/arms/{arm_id}"),
    ("PATCH", "/api/v1/strategies/{strategy_id}"),
    ("POST", "/api/v1/agent/keys"),
    ("POST", "/api/v1/backtests"),
    ("POST", "/api/v1/graph/dry-run"),
    ("POST", "/api/v1/graph/validate"),
    ("POST", "/api/v1/paper/approvals/{approval_id}/approve"),
    ("POST", "/api/v1/paper/approvals/{approval_id}/reject"),
    ("POST", "/api/v1/paper/orders"),
    ("POST", "/api/v1/plugins/reload"),
    ("POST", "/api/v1/plugins/{plugin_id}/validate"),
    ("POST", "/api/v1/protection/arms"),
    ("POST", "/api/v1/protection/arms/{arm_id}/cancel"),
    ("POST", "/api/v1/protection/arms/{arm_id}/evaluate"),
    ("POST", "/api/v1/protection/profiles"),
    ("POST", "/api/v1/provider-keys"),
    ("POST", "/api/v1/strategies"),
    ("POST", "/api/v1/strategies/{strategy_id}/run"),
    ("POST", "/api/v1/system/kill-switch"),
    ("PUT", "/api/v1/paper/automation"),
    ("PUT", "/api/v1/trade-policy/strategies/{strategy_id}"),
)


def make_client(
    settings: Settings,
    session: Session,
    session_factory=None,
    *,
    peer: tuple[str, int] = REMOTE_PEER,
) -> TestClient:
    app = FastAPI()
    app.add_middleware(
        BearerAuthMiddleware,
        settings=settings,
        session_factory=session_factory,
    )
    app.include_router(system.router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app, client=peer)


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


# --- operational endpoint boundary ----------------------------------------


def test_operational_classification_is_explicit(protected_settings: Settings) -> None:
    assert OPERATIONAL_PATHS == {"/health", "/metrics"}
    assert LOOPBACK_ONLY_PATHS == {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}
    assert AUTHENTICATED_READ_PATHS == {"/api/v1/agent/state"}
    for path in ("/health", "/metrics"):
        assert requires_auth("GET", path, protected_settings) is True
        assert required_scope("GET", path) == "admin"
    assert requires_auth("GET", "/api/v1/agent/state", protected_settings) is True
    assert required_scope("GET", "/api/v1/agent/state") == "read"
    for path in ("/healthz", "/readyz"):
        assert requires_auth("GET", path, protected_settings) is False


def test_the_agent_key_route_requires_admin_even_to_read() -> None:
    """The MCP credential probe depends on this classification.

    ``ezchain-mcp`` probes ``GET /api/v1/agent/keys`` to tell a scoped agent key (403
    ``FORBIDDEN``) from an admin principal (200). If a read on that route stopped needing
    ``admin``, a scoped key would get 200 and be misread as the deployment token, so the
    pair of facts the probe rests on is pinned here.
    """
    for path in ("/api/v1/agent/keys", "/api/v1/agent/keys/key-1"):
        assert required_scope("GET", path) == "admin"
        assert required_scope("POST", path) == "admin"


def test_every_root_route_is_classified() -> None:
    app = create_app()
    root = {
        route.path_format
        for route in _api_routes(list(app.routes))
        if not route.path_format.startswith("/api/")
    }

    assert root == {"/healthz", "/readyz"} | set(OPERATIONAL_PATHS), (
        "classify a new root route as public-safe or operational"
    )


def test_metrics_is_available_from_loopback_without_a_token(
    protected_settings: Settings, session: Session
) -> None:
    client = make_client(protected_settings, session, peer=LOCAL_PEER)

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    for line in METRICS_CONTRACT:
        assert line in response.text


@pytest.mark.parametrize(
    "peer",
    [
        ("127.0.0.1", 50000),
        ("::1", 50000),
        ("::ffff:127.0.0.1", 50000),
    ],
)
def test_loopback_address_forms_are_accepted(
    protected_settings: Settings, session: Session, peer: tuple[str, int]
) -> None:
    assert make_client(protected_settings, session, peer=peer).get("/metrics").status_code == 200


def test_metrics_rejects_a_remote_client_without_a_token(
    protected_settings: Settings, session: Session
) -> None:
    client = make_client(protected_settings, session, peer=REMOTE_PEER)

    response = client.get("/metrics")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"
    assert response.json()["detail"]["retryable"] is False


def test_metrics_accepts_the_operator_token_from_a_remote_client(
    protected_settings: Settings, session: Session
) -> None:
    session.add(
        PaperOrderRow(
            idempotency_key="remote-metrics-order",
            chain="solana",
            token_address="token-address",
            side="buy",
            amount_usd=Decimal("10"),
            status="filled",
        )
    )
    session.commit()
    client = make_client(protected_settings, session, peer=REMOTE_PEER)

    accepted = client.get(
        "/metrics",
        headers={"authorization": "Bearer correct-token"},
    )
    rejected = client.get(
        "/metrics",
        headers={"authorization": "Bearer not-the-token"},
    )

    assert accepted.status_code == 200
    for line in METRICS_CONTRACT:
        assert line in accepted.text
    # The authenticated remote scrape sees the same paper counters as a local one.
    assert 'ezchain_paper_orders_total{status="filled"} 1' in accepted.text
    assert rejected.status_code == 401


def test_health_requires_a_token_from_a_remote_client(
    protected_settings: Settings, session: Session
) -> None:
    remote = make_client(protected_settings, session, peer=REMOTE_PEER)
    local = make_client(protected_settings, session, peer=LOCAL_PEER)

    assert remote.get("/health").status_code == 401
    assert local.get("/health").status_code == 200
    assert (
        remote.get("/health", headers={"authorization": "Bearer correct-token"}).status_code == 200
    )


def test_public_probes_stay_public_from_a_remote_client(
    protected_settings: Settings, session: Session
) -> None:
    client = make_client(protected_settings, session, peer=REMOTE_PEER)

    assert client.get("/healthz").status_code == 200
    # The session fixture has no Alembic stamp, so readiness reports 503; the point is
    # that it answers without a credential rather than returning 401.
    assert client.get("/readyz").status_code == 503


def test_spoofed_forwarded_headers_do_not_grant_loopback(
    protected_settings: Settings, session: Session
) -> None:
    client = make_client(protected_settings, session, peer=REMOTE_PEER)

    response = client.get(
        "/metrics",
        headers={
            "x-forwarded-for": "127.0.0.1",
            "x-real-ip": "127.0.0.1",
            "forwarded": "for=127.0.0.1",
        },
    )

    assert response.status_code == 401


def test_a_same_host_proxy_does_not_launder_a_remote_client(
    protected_settings: Settings, session: Session
) -> None:
    proxy = make_client(protected_settings, session, peer=LOCAL_PEER)

    # A local browser through a local proxy is still local.
    assert proxy.get("/metrics", headers={"x-forwarded-for": "127.0.0.1"}).status_code == 200
    # A public client through that same proxy is not.
    assert proxy.get("/metrics", headers={"x-forwarded-for": "203.0.113.9"}).status_code == 401
    assert (
        proxy.get("/metrics", headers={"x-forwarded-for": "203.0.113.9, 127.0.0.1"}).status_code
        == 401
    )


def test_a_configured_trusted_proxy_can_report_a_loopback_client(
    protected_settings: Settings, session: Session
) -> None:
    proxied = protected_settings.model_copy(update={"trusted_proxy_cidrs": ["10.0.0.0/8"]})
    proxy = make_client(proxied, session, peer=("10.1.2.3", 50000))

    # One trusted hop in front of a local browser or scrape.
    assert proxy.get("/metrics", headers={"x-forwarded-for": "127.0.0.1"}).status_code == 200
    # A real remote client stays remote even when it sends the same header.
    assert proxy.get("/metrics", headers={"x-forwarded-for": "198.51.100.7"}).status_code == 401
    # Walk past trusted hops to the first address the proxy did not add.
    assert (
        proxy.get("/metrics", headers={"x-forwarded-for": "127.0.0.1, 10.2.3.4"}).status_code == 200
    )
    # A peer outside the trusted list is never allowed to supply the header.
    direct = make_client(proxied, session, peer=("198.51.100.7", 50000))
    assert direct.get("/metrics", headers={"x-forwarded-for": "127.0.0.1"}).status_code == 401


def test_remote_operational_request_fails_closed_when_auth_is_disabled(
    settings: Settings, session: Session
) -> None:
    disabled = settings.model_copy(update={"auth_enabled": False, "api_token": None})

    remote = make_client(disabled, session, peer=REMOTE_PEER)
    local = make_client(disabled, session, peer=LOCAL_PEER)

    assert remote.get("/metrics").status_code == 401
    assert local.get("/metrics").status_code == 200


def test_interactive_schema_is_loopback_only(
    protected_settings: Settings, session: Session
) -> None:
    remote = make_client(protected_settings, session, peer=REMOTE_PEER)
    local = make_client(protected_settings, session, peer=LOCAL_PEER)

    for path in ("/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"):
        assert remote.get(path).status_code == 404, path
        assert local.get(path).status_code == 200, path


def test_agent_state_needs_a_principal_even_from_loopback(
    protected_settings: Settings,
) -> None:
    app = FastAPI()
    app.add_middleware(BearerAuthMiddleware, settings=protected_settings)

    @app.get("/api/v1/agent/state")
    def state() -> dict[str, bool]:
        return {"ok": True}

    remote = TestClient(app, client=REMOTE_PEER)
    local = TestClient(app, client=LOCAL_PEER)
    headers = {"authorization": "Bearer correct-token"}

    assert remote.get("/api/v1/agent/state").status_code == 401
    assert local.get("/api/v1/agent/state").status_code == 401
    assert remote.get("/api/v1/agent/state", headers=headers).status_code == 200
    assert local.get("/api/v1/agent/state", headers=headers).status_code == 200


def test_trusted_proxy_list_refuses_a_typo_or_the_whole_internet() -> None:
    with pytest.raises(ValidationError, match="EZCHAIN_TRUSTED_PROXY_CIDRS"):
        Settings(environment="dev", trusted_proxy_cidrs=["not-a-network"])
    with pytest.raises(ValidationError, match="must not trust every address"):
        Settings(environment="dev", trusted_proxy_cidrs=["0.0.0.0/0"])

    parsed = Settings(
        environment="dev",
        trusted_proxy_cidrs=[" 10.0.0.0/8 ", "", "2001:db8::1"],
    )
    assert parsed.trusted_proxy_cidrs == ["10.0.0.0/8", "2001:db8::1"]
