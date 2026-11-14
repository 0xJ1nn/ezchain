"""The bucket surface: read the deployment, set the cap, and refuse the wrong principal.

Two surfaces, one shape. ``GET /api/v1/agent/buckets`` reports each capped agent with its
cap and the deployment replayed from the ledger; ``PUT /api/v1/agent/buckets/{agent_id}``
sets a cap. Only a person, and only the agent's own owner, may set one.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from test_order_actor import DEPLOYMENT, PEPPER, FakeDex

from ezchain_adapters import QuoteRouter
from ezchain_api.actor import ACTOR_HUMAN, OrderActor
from ezchain_api.auth import BearerAuthMiddleware
from ezchain_api.config import Settings
from ezchain_api.deps import get_dexscreener, get_quote_router, get_session
from ezchain_api.models import AgentBucketRow
from ezchain_api.routers import buckets as buckets_router
from ezchain_api.services import api_keys, buckets

pytestmark = pytest.mark.anyio


@pytest.fixture
def authed_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"api_token": SecretStr(DEPLOYMENT), "api_key_pepper": SecretStr(PEPPER)}
    )


@pytest.fixture
def key_factory(engine: Any) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def _client(settings: Settings, session: Session, key_factory: sessionmaker[Session]) -> TestClient:
    source = FakeDex()
    app = FastAPI()
    app.add_middleware(BearerAuthMiddleware, settings=settings, session_factory=key_factory)
    app.include_router(buckets_router.router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_dexscreener] = lambda: source
    app.dependency_overrides[get_quote_router] = lambda: QuoteRouter(
        providers=[],
        dexscreener=source,  # type: ignore[arg-type]
    )
    return TestClient(app)


def _agent_token(key_factory: sessionmaker[Session], subject: str = "alice") -> tuple[str, str]:
    with key_factory() as session:
        row, token = api_keys.create_key(
            session, subject=subject, scopes=["trade"], expires_in_days=30, pepper=PEPPER
        )
        return row.id, token


def _bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def test_a_person_sets_a_cap_and_reads_it_back_with_the_deployment(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    """The deployment principal is the person, so its own agents are the ones it may bound."""
    key_id, _ = _agent_token(key_factory, subject="deployment")
    client = _client(authed_settings, session, key_factory)

    set_response = client.put(
        f"/api/v1/agent/buckets/{key_id}", json={"cap_usd": "250"}, headers=_bearer(DEPLOYMENT)
    )

    assert set_response.status_code == 200
    body = set_response.json()
    assert body["agent_id"] == key_id
    assert Decimal(body["cap_usd"]) == Decimal("250")
    assert Decimal(body["deployed_usd"]) == Decimal("0")
    assert Decimal(body["available_usd"]) == Decimal("250")
    assert body["owner"] == "deployment"
    assert body["updated_by"] == {"kind": "human", "id": "deployment"}

    listed = client.get("/api/v1/agent/buckets", headers=_bearer(DEPLOYMENT)).json()
    assert [row["agent_id"] for row in listed["buckets"]] == [key_id]
    assert listed["buckets"][0]["available_usd"] == "250"


def test_an_agent_key_may_read_buckets(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    key_id, token = _agent_token(key_factory, subject="deployment")
    client = _client(authed_settings, session, key_factory)
    client.put(
        f"/api/v1/agent/buckets/{key_id}", json={"cap_usd": "10"}, headers=_bearer(DEPLOYMENT)
    )

    listed = client.get("/api/v1/agent/buckets", headers=_bearer(token))

    assert listed.status_code == 200
    assert listed.json()["buckets"][0]["agent_id"] == key_id


def test_an_agent_key_cannot_set_its_own_cap(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    key_id, token = _agent_token(key_factory)
    client = _client(authed_settings, session, key_factory)

    response = client.put(
        f"/api/v1/agent/buckets/{key_id}", json={"cap_usd": "100000"}, headers=_bearer(token)
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "AGENT_BUCKET_REQUIRES_PERSON"
    assert session.get(AgentBucketRow, key_id) is None


def test_another_users_key_cannot_move_an_agents_cap(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    """A person may only bound an agent that belongs to them.

    ``alice``'s agent is provisioned through the deployment principal's own key (the
    admin token mints keys for any subject), and the deployment principal must not then
    change it: the boundary belongs to alice's user, not to the person who minted the key.
    """
    from ezchain_api.services import approvals

    alice_key, _ = _agent_token(key_factory, subject="alice")
    client = _client(authed_settings, session, key_factory)

    # alice, a person, sets her own agent's cap through the service surface.
    approvals.set_tier(
        session,
        tier="manual",
        actor=OrderActor(ACTOR_HUMAN, "alice-user", owner="alice"),
        settings=authed_settings,
    )
    buckets.set_cap(
        session,
        alice_key,
        Decimal("100"),
        actor=OrderActor(ACTOR_HUMAN, "alice-user", owner="alice"),
    )

    response = client.put(
        f"/api/v1/agent/buckets/{alice_key}",
        json={"cap_usd": "9999"},
        headers=_bearer(DEPLOYMENT),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "AGENT_BUCKET_REQUIRES_PERSON"
    row = session.get(AgentBucketRow, alice_key)
    assert row is not None and Decimal(str(row.cap_usd)) == Decimal("100")


def test_a_negative_cap_is_422_and_writes_nothing(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    key_id, _ = _agent_token(key_factory)
    client = _client(authed_settings, session, key_factory)

    response = client.put(
        f"/api/v1/agent/buckets/{key_id}", json={"cap_usd": "-5"}, headers=_bearer(DEPLOYMENT)
    )

    assert response.status_code == 422
    assert session.scalar(select(AgentBucketRow.agent_id)) is None


def test_a_body_cannot_carry_a_balance_or_an_agent_id(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    """The cap is the only settable field: a balance is the ledger's, never a request field."""
    key_id, _ = _agent_token(key_factory)
    client = _client(authed_settings, session, key_factory)

    for extra in ({"deployed_usd": "0"}, {"agent_id": "someone-else"}, {"balance_usd": "9999"}):
        response = client.put(
            f"/api/v1/agent/buckets/{key_id}",
            json={"cap_usd": "10", **extra},
            headers=_bearer(DEPLOYMENT),
        )
        assert response.status_code == 422, extra
    assert session.get(AgentBucketRow, key_id) is None


def test_an_unknown_agent_has_no_bucket(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    client = _client(authed_settings, session, key_factory)

    response = client.get("/api/v1/agent/buckets/never-minted", headers=_bearer(DEPLOYMENT))

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "AGENT_BUCKET_UNKNOWN"
