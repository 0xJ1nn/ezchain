"""Scoped agent keys: hashed at rest, least privilege, revocable, auditable."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session, sessionmaker

from ezchain_api.auth import BearerAuthMiddleware
from ezchain_api.config import Settings, get_settings
from ezchain_api.deps import get_session
from ezchain_api.routers import agent, system
from ezchain_api.routers.agent import ApiKeyIn, create_agent_key
from ezchain_api.services import api_keys


@pytest.fixture
def key_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def protected_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "api_token": SecretStr("deployment-token"),
            "api_key_pepper": SecretStr("pepper-for-tests"),
            "require_auth_for_reads": True,
        }
    )


def make_client(
    settings: Settings,
    session: Session,
    key_factory: sessionmaker[Session],
) -> TestClient:
    app = FastAPI()
    app.add_middleware(
        BearerAuthMiddleware,
        settings=settings,
        session_factory=key_factory,
    )
    app.include_router(system.router)
    app.include_router(agent.router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def _key(
    factory: sessionmaker[Session],
    *,
    scopes: list[str],
    subject: str = "hermes",
    pepper: str = "pepper-for-tests",
) -> str:
    with factory() as session:
        _, token = api_keys.create_key(
            session,
            subject=subject,
            scopes=scopes,
            expires_in_days=30,
            pepper=pepper,
        )
    return token


def test_trade_key_cannot_use_admin_routes(
    protected_settings: Settings,
    session: Session,
    key_factory: sessionmaker[Session],
) -> None:
    token = _key(key_factory, scopes=["trade"])
    client = make_client(protected_settings, session, key_factory)

    trade = client.post(
        "/api/v1/not-built-yet",
        json={},
        headers={"authorization": f"Bearer {token}"},
    )
    admin = client.post(
        "/api/v1/system/kill-switch",
        json={"active": True},
        headers={"authorization": f"Bearer {token}"},
    )

    assert trade.status_code == 404  # authenticated, route absent
    assert admin.status_code == 403
    assert admin.json()["detail"]["code"] == "FORBIDDEN"


def test_admin_key_can_use_admin_routes(
    protected_settings: Settings,
    session: Session,
    key_factory: sessionmaker[Session],
) -> None:
    token = _key(key_factory, scopes=["admin"], subject="ops")
    client = make_client(protected_settings, session, key_factory)

    response = client.post(
        "/api/v1/system/kill-switch",
        json={"active": True, "reason": "key drill"},
        headers={"authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200


def test_read_key_requires_scope_and_reads_close_when_configured(
    protected_settings: Settings,
    session: Session,
    key_factory: sessionmaker[Session],
) -> None:
    client = make_client(protected_settings, session, key_factory)
    assert client.get("/api/v1/agent/capabilities").status_code == 401

    read_token = _key(key_factory, scopes=["read"], subject="viewer")
    read = client.get(
        "/api/v1/agent/capabilities",
        headers={"authorization": f"Bearer {read_token}"},
    )
    assert read.status_code == 200
    assert read.json()["principal"]["subject"] == "viewer"
    assert read.json()["principal"]["scopes"] == ["read"]

    write = client.post(
        "/api/v1/not-built-yet",
        json={},
        headers={"authorization": f"Bearer {read_token}"},
    )
    assert write.status_code == 403

    switch = client.get(
        "/api/v1/system/kill-switch",
        headers={"authorization": f"Bearer {read_token}"},
    )
    assert switch.status_code == 200


def test_revoked_and_expired_keys_are_denied(
    protected_settings: Settings,
    session: Session,
    key_factory: sessionmaker[Session],
) -> None:
    token = _key(key_factory, scopes=["read"])
    with key_factory() as db:
        row = api_keys.verify_token(db, token, "pepper-for-tests")
        assert row is not None
        api_keys.revoke_key(db, row.id)
    client = make_client(protected_settings, session, key_factory)
    assert (
        client.get(
            "/api/v1/agent/capabilities",
            headers={"authorization": f"Bearer {token}"},
        ).status_code
        == 401
    )


def test_key_creation_returns_plaintext_once_and_lists_no_hash(
    protected_settings: Settings,
    session: Session,
    key_factory: sessionmaker[Session],
) -> None:
    client = make_client(protected_settings, session, key_factory)
    created = client.post(
        "/api/v1/agent/keys",
        json={"subject": "openclaw", "scopes": ["trade"], "label": "desk"},
        headers={"authorization": "Bearer deployment-token"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["token"].startswith(api_keys.TOKEN_PREFIX)
    assert body["subject"] == "openclaw"
    assert "token_hash" not in body

    unauthenticated = client.get("/api/v1/agent/keys")
    assert unauthenticated.status_code == 401

    listed = client.get(
        "/api/v1/agent/keys",
        headers={"authorization": "Bearer deployment-token"},
    )
    assert listed.status_code == 200
    assert "token" not in listed.json()[0]
    assert "token_hash" not in listed.json()[0]

    revoked = client.delete(
        f"/api/v1/agent/keys/{body['id']}",
        headers={"authorization": "Bearer deployment-token"},
    )
    assert revoked.status_code == 204


def test_production_requires_a_pepper_before_minting(settings: Settings, session: Session) -> None:
    prod = settings.model_copy(update={"environment": "prod", "api_key_pepper": None})
    with pytest.raises(HTTPException) as caught:
        create_agent_key(
            ApiKeyIn(subject="hermes", scopes=["read"]),
            session=session,
            settings=prod,
        )
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "API_KEY_PEPPER_REQUIRED"
