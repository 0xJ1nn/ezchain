"""The approval API and the per-user automation tier.

An agent order waits in the queue under ``manual``; a person approves or rejects it and
the decision is attributed. ``full_auto`` is an explicit opt-in that requires the risk
acknowledgement, and only then does an agent order skip the queue.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from test_order_actor import CHAIN, DEPLOYMENT, PEPPER, TOKEN, FakeDex

from ezchain_adapters import QuoteRouter
from ezchain_api.actor import ACTOR_AGENT, ACTOR_HUMAN
from ezchain_api.auth import BearerAuthMiddleware
from ezchain_api.config import Settings
from ezchain_api.deps import get_dexscreener, get_quote_router, get_session
from ezchain_api.models import (
    AutomationConsentRow,
    AutomationSettingRow,
    PaperFillRow,
    PaperOrderRow,
)
from ezchain_api.routers import approvals as approvals_router
from ezchain_api.routers import paper as paper_router
from ezchain_api.services import api_keys

pytestmark = pytest.mark.anyio


@pytest.fixture
def authed_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "api_token": SecretStr(DEPLOYMENT),
            "api_key_pepper": SecretStr(PEPPER),
        }
    )


@pytest.fixture
def key_factory(engine: Any) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def _client(settings: Settings, session: Session, key_factory: sessionmaker[Session]) -> TestClient:
    source = FakeDex()
    app = FastAPI()
    app.add_middleware(BearerAuthMiddleware, settings=settings, session_factory=key_factory)
    app.include_router(paper_router.router)
    app.include_router(approvals_router.router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_dexscreener] = lambda: source
    app.dependency_overrides[get_quote_router] = lambda: QuoteRouter(
        providers=[],
        dexscreener=source,  # type: ignore[arg-type]
    )
    return TestClient(app)


def _agent_token(key_factory: sessionmaker[Session], subject: str = "hermes") -> str:
    with key_factory() as session:
        _, token = api_keys.create_key(
            session,
            subject=subject,
            scopes=["trade"],
            expires_in_days=30,
            pepper=PEPPER,
        )
        return token


def _payload(key: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "chain": CHAIN,
        "token_address": TOKEN,
        "side": "buy",
        "amount_usd": "25",
        "idempotency_key": key,
    }
    body.update(overrides)
    return body


def _bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def test_an_agent_order_returns_the_approval_id_and_status(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    client = _client(authed_settings, session, key_factory)
    token = _agent_token(key_factory)

    response = client.post(
        "/api/v1/paper/orders", json=_payload("api-pending-1"), headers=_bearer(token)
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending_approval"
    assert body["reason"] == "AWAITING_APPROVAL"
    assert body["approval_status"] == "pending"
    assert body["approval_id"]
    assert session.scalar(select(PaperFillRow.id)) is None


def test_an_agent_key_may_read_the_queue_but_not_decide(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    client = _client(authed_settings, session, key_factory)
    token = _agent_token(key_factory)
    approval_id = client.post(
        "/api/v1/paper/orders", json=_payload("api-agent-read-1"), headers=_bearer(token)
    ).json()["approval_id"]

    listed = client.get("/api/v1/paper/approvals", headers=_bearer(token))
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [approval_id]

    approved = client.post(f"/api/v1/paper/approvals/{approval_id}/approve", headers=_bearer(token))
    assert approved.status_code == 403
    assert approved.json()["detail"]["code"] == "APPROVAL_REQUIRES_PERSON"


def test_a_person_approves_through_the_api_and_the_order_fills(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    client = _client(authed_settings, session, key_factory)
    agent = _agent_token(key_factory)
    posted = client.post(
        "/api/v1/paper/orders", json=_payload("api-approve-1"), headers=_bearer(agent)
    ).json()

    approved = client.post(
        f"/api/v1/paper/approvals/{posted['approval_id']}/approve",
        json={"note": "looks fine"},
        headers=_bearer(DEPLOYMENT),
    )

    assert approved.status_code == 200
    body = approved.json()
    assert body["status"] == "executed"
    assert body["decided_by"] == {"kind": ACTOR_HUMAN, "id": "deployment"}
    assert body["decision_note"] == "looks fine"
    order = session.get(PaperOrderRow, posted["id"])
    assert order is not None
    assert order.status == "filled"
    assert (order.actor_kind, order.actor_id) == (ACTOR_AGENT, order.actor_id)


def test_a_person_rejects_through_the_api_with_a_reason(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    client = _client(authed_settings, session, key_factory)
    agent = _agent_token(key_factory)
    posted = client.post(
        "/api/v1/paper/orders", json=_payload("api-reject-1"), headers=_bearer(agent)
    ).json()

    rejected = client.post(
        f"/api/v1/paper/approvals/{posted['approval_id']}/reject",
        json={"note": "not this token"},
        headers=_bearer(DEPLOYMENT),
    )

    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    order = session.get(PaperOrderRow, posted["id"])
    assert order is not None
    assert order.status == "rejected"
    assert order.reason == "APPROVAL_REJECTED"
    assert session.scalar(select(PaperFillRow.id)) is None


def test_a_rejection_without_a_note_is_a_validation_error(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    client = _client(authed_settings, session, key_factory)
    agent = _agent_token(key_factory)
    approval_id = client.post(
        "/api/v1/paper/orders", json=_payload("api-reject-nonote-1"), headers=_bearer(agent)
    ).json()["approval_id"]

    response = client.post(
        f"/api/v1/paper/approvals/{approval_id}/reject", json={}, headers=_bearer(DEPLOYMENT)
    )

    assert response.status_code == 422


def test_the_tier_defaults_to_manual_and_names_the_disclosure(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    client = _client(authed_settings, session, key_factory)

    body = client.get("/api/v1/paper/automation").json()

    assert body["tier"] == "manual"
    assert body["tiers"] == ["manual", "full_auto"]
    assert body["agent_orders"] == "wait_for_a_person"
    assert body["disclosure"]["acknowledged_at"] is None
    assert "you own the risk" in body["disclosure"]["text"].lower()


def test_switching_to_full_auto_needs_the_acknowledgement(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    client = _client(authed_settings, session, key_factory)

    refused = client.put(
        "/api/v1/paper/automation",
        json={"tier": "full_auto"},
        headers=_bearer(DEPLOYMENT),
    )
    assert refused.status_code == 422
    assert session.scalar(select(AutomationSettingRow.user_id)) is None


def test_full_auto_opt_in_records_the_consent_with_the_user_and_time(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    client = _client(authed_settings, session, key_factory)

    accepted = client.put(
        "/api/v1/paper/automation",
        json={"tier": "full_auto", "acknowledge_risk": True},
        headers=_bearer(DEPLOYMENT),
    )

    assert accepted.status_code == 200
    body = accepted.json()
    assert body["tier"] == "full_auto"
    assert body["agent_orders"] == "execute_automatically"
    assert body["disclosure"]["acknowledged_at"] is not None
    consent = session.scalar(select(AutomationConsentRow))
    assert consent is not None
    assert consent.user_id == authed_settings.workspace_id
    assert consent.tier == "full_auto"
    assert consent.actor_kind == ACTOR_HUMAN
    assert consent.acknowledged_at is not None


def test_an_agent_cannot_change_the_tier(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    client = _client(authed_settings, session, key_factory)
    agent = _agent_token(key_factory)

    response = client.put(
        "/api/v1/paper/automation",
        json={"tier": "full_auto", "acknowledge_risk": True},
        headers=_bearer(agent),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "APPROVAL_REQUIRES_PERSON"
    assert session.scalar(select(AutomationSettingRow.user_id)) is None


def test_a_full_auto_user_gets_agent_orders_that_fill_without_an_approval(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    client = _client(authed_settings, session, key_factory)
    client.put(
        "/api/v1/paper/automation",
        json={"tier": "full_auto", "acknowledge_risk": True},
        headers=_bearer(DEPLOYMENT),
    )
    agent = _agent_token(key_factory)

    response = client.post(
        "/api/v1/paper/orders", json=_payload("api-fullauto-1"), headers=_bearer(agent)
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "filled"
    assert body["approval_id"] is None
    assert body["approval_status"] is None


def test_returning_to_manual_keeps_the_consent_history_and_gates_again(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    client = _client(authed_settings, session, key_factory)
    client.put(
        "/api/v1/paper/automation",
        json={"tier": "full_auto", "acknowledge_risk": True},
        headers=_bearer(DEPLOYMENT),
    )

    back = client.put(
        "/api/v1/paper/automation", json={"tier": "manual"}, headers=_bearer(DEPLOYMENT)
    )
    assert back.json()["tier"] == "manual"
    assert session.scalar(select(AutomationConsentRow.id)) is not None

    agent = _agent_token(key_factory)
    gated = client.post(
        "/api/v1/paper/orders", json=_payload("api-back-manual-1"), headers=_bearer(agent)
    ).json()
    assert gated["status"] == "pending_approval"


def test_a_full_auto_row_without_its_consent_reads_as_manual(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    """An opt-in whose acknowledgement is missing never lets an agent order through."""
    session.add(AutomationSettingRow(user_id=authed_settings.workspace_id, tier="full_auto"))
    session.commit()
    client = _client(authed_settings, session, key_factory)

    assert client.get("/api/v1/paper/automation").json()["tier"] == "manual"

    agent = _agent_token(key_factory)
    posted = client.post(
        "/api/v1/paper/orders", json=_payload("api-orphan-auto-1"), headers=_bearer(agent)
    ).json()
    assert posted["status"] == "pending_approval"


def test_a_human_order_is_never_queued_under_manual(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    client = _client(authed_settings, session, key_factory)

    response = client.post(
        "/api/v1/paper/orders", json=_payload("api-human-1"), headers=_bearer(DEPLOYMENT)
    )

    assert response.status_code == 201
    assert response.json()["status"] == "filled"
    assert response.json()["approval_id"] is None
    assert response.json()["actor"] == {"kind": ACTOR_HUMAN, "id": "deployment"}
