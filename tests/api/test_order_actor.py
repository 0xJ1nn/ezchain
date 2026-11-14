"""The actor is a server fact, never a request field.

A manual or MCP order names the authenticated caller, a strategy order names the strategy,
and anything that cannot be determined is recorded as unknown.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ezchain_adapters import QuoteRouter
from ezchain_api.actor import (
    ACTOR_AGENT,
    ACTOR_HUMAN,
    ACTOR_UNKNOWN,
    OrderActor,
    from_principal,
)
from ezchain_api.auth import BearerAuthMiddleware
from ezchain_api.config import Settings
from ezchain_api.deps import get_dexscreener, get_quote_router, get_session
from ezchain_api.models import ApiKeyRow, LedgerEntryRow, PaperOrderRow, new_id
from ezchain_api.routers import account as account_router
from ezchain_api.routers import paper as paper_router
from ezchain_api.services import api_keys
from ezchain_core.types import PairInfo, SourceRef, TokenSnapshot, utcnow

CHAIN = "blast"
TOKEN = "0x0000000000000000000000000000000000000abc"
PEPPER = "pepper-for-tests"
DEPLOYMENT = "deployment-token"


class FakeDex:
    def __init__(self, price: Decimal | None = Decimal("2")) -> None:
        self.price = price

    async def get_token(
        self, chain: str, address: str, *, max_age_seconds: float | None = None
    ) -> TokenSnapshot:
        observed = datetime.now(UTC)
        return TokenSnapshot(
            chain=chain,
            address=address,
            symbol="TEST",
            name="Test",
            price_usd=self.price,
            liquidity_usd=Decimal("500000"),
            volume_24h_usd=Decimal("1000000"),
            price_change_m5=Decimal("0"),
            price_change_h1=Decimal("1"),
            price_change_h6=Decimal("0"),
            price_change_h24=Decimal("0"),
            pairs=(
                PairInfo(
                    "0xpair",
                    "uniswap",
                    "USDC",
                    self.price or Decimal(0),
                    Decimal("500000"),
                    Decimal("1000000"),
                    None,
                ),
            ),
            source=SourceRef("dexscreener", None, observed),
            pair_created_at=datetime(2020, 1, 1, tzinfo=UTC),
        )


@pytest.fixture
def key_factory(engine: Any) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def authed_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "api_token": SecretStr(DEPLOYMENT),
            "api_key_pepper": SecretStr(PEPPER),
        }
    )


def _client(
    settings: Settings,
    session: Session,
    key_factory: sessionmaker[Session],
    *,
    dex: FakeDex | None = None,
) -> TestClient:
    source = dex or FakeDex()
    app = FastAPI()
    app.add_middleware(BearerAuthMiddleware, settings=settings, session_factory=key_factory)
    app.include_router(paper_router.router)
    app.include_router(account_router.router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_dexscreener] = lambda: source
    app.dependency_overrides[get_quote_router] = lambda: QuoteRouter(
        providers=[],
        dexscreener=source,  # type: ignore[arg-type]
    )
    return TestClient(app)


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


def _agent_token(key_factory: sessionmaker[Session]) -> tuple[str, str]:
    with key_factory() as session:
        row, token = api_keys.create_key(
            session,
            subject="hermes",
            scopes=["trade"],
            expires_in_days=30,
            pepper=PEPPER,
        )
        return row.id, token


def test_the_deployment_token_is_a_human_actor(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    client = _client(authed_settings, session, key_factory)

    response = client.post(
        "/api/v1/paper/orders",
        json=_payload("actor-human-1"),
        headers={"authorization": f"Bearer {DEPLOYMENT}"},
    )

    assert response.status_code == 201
    assert response.json()["actor"] == {"kind": ACTOR_HUMAN, "id": "deployment"}


def test_an_agent_key_is_an_agent_actor_addressed_by_key_id(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    key_id, token = _agent_token(key_factory)
    client = _client(authed_settings, session, key_factory)

    response = client.post(
        "/api/v1/paper/orders",
        json=_payload("actor-agent-1"),
        headers={"authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    assert response.json()["actor"] == {"kind": ACTOR_AGENT, "id": key_id}
    assert session.scalar(select(ApiKeyRow.id)) == key_id


def test_a_request_without_a_principal_records_unknown(
    settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    """The development fallback has no caller, so the server records unknown."""
    client = _client(settings, session, key_factory)

    response = client.post("/api/v1/paper/orders", json=_payload("actor-unknown-1"))

    assert response.status_code == 201
    assert response.json()["actor"] == {"kind": ACTOR_UNKNOWN, "id": None}


def test_a_client_cannot_supply_its_own_actor(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    client = _client(authed_settings, session, key_factory)

    response = client.post(
        "/api/v1/paper/orders",
        json=_payload("actor-forged-1", actor={"kind": "human", "id": "someone-else"}),
        headers={"authorization": f"Bearer {DEPLOYMENT}"},
    )

    assert response.status_code == 422
    assert [error["loc"] for error in response.json()["detail"]] == [["body", "actor"]]
    assert session.scalar(select(PaperOrderRow)) is None


def test_a_replay_keeps_the_original_actor(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    """A second caller cannot re-attribute an order by replaying its key."""
    _, token = _agent_token(key_factory)
    client = _client(authed_settings, session, key_factory)
    payload = _payload("actor-replay-1")

    first = client.post(
        "/api/v1/paper/orders",
        json=payload,
        headers={"authorization": f"Bearer {DEPLOYMENT}"},
    )
    replay = client.post(
        "/api/v1/paper/orders",
        json=payload,
        headers={"authorization": f"Bearer {token}"},
    )

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["actor"] == {"kind": ACTOR_HUMAN, "id": "deployment"}


def test_every_journal_leg_carries_the_order_actor(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    client = _client(authed_settings, session, key_factory)

    response = client.post(
        "/api/v1/paper/orders",
        json=_payload("actor-journal-1"),
        headers={"authorization": f"Bearer {DEPLOYMENT}"},
    )
    order_id = response.json()["id"]

    rows = list(session.scalars(select(LedgerEntryRow).where(LedgerEntryRow.group_id == order_id)))
    assert rows
    assert {(row.actor_kind, row.actor_id) for row in rows} == {(ACTOR_HUMAN, "deployment")}


def test_the_ledger_read_model_shows_the_actor(
    authed_settings: Settings, session: Session, key_factory: sessionmaker[Session]
) -> None:
    client = _client(authed_settings, session, key_factory)
    order_id = client.post(
        "/api/v1/paper/orders",
        json=_payload("actor-ledger-read-1"),
        headers={"authorization": f"Bearer {DEPLOYMENT}"},
    ).json()["id"]

    body = client.get("/api/v1/paper/ledger", params={"limit": 50}).json()
    legs = [row for row in body if row["group_id"] == order_id]
    assert legs
    assert {row["actor_kind"] for row in legs} == {ACTOR_HUMAN}
    assert {row["actor_id"] for row in legs} == {"deployment"}
    genesis = next(row for row in body if row["group_id"] == "genesis")
    assert genesis["actor_kind"] == ACTOR_UNKNOWN
    assert genesis["actor_id"] is None


def test_a_legacy_order_reads_as_unknown(client_for: Any, session: Session) -> None:
    """A row written before the columns existed is unknown, not attributed after the fact."""
    session.add(
        PaperOrderRow(
            id=new_id(),
            idempotency_key="actor-legacy-1",
            chain=CHAIN,
            token_address=TOKEN,
            side="buy",
            amount_usd=Decimal("25"),
            status="rejected",
            reason="NO_ROUTE",
            created_at=utcnow(),
        )
    )
    session.commit()

    client: TestClient = client_for(paper_router.router)
    body = client.get("/api/v1/paper/orders").json()

    assert body[0]["actor"] == {"kind": ACTOR_UNKNOWN, "id": None}


def test_an_actor_kind_must_be_addressable() -> None:
    with pytest.raises(ValueError):
        OrderActor("nobody", None)
    with pytest.raises(ValueError):
        OrderActor(ACTOR_HUMAN, None)
    with pytest.raises(ValueError):
        OrderActor(ACTOR_UNKNOWN, "someone")


def test_from_principal_is_explicit_about_what_it_cannot_name() -> None:
    class Principal:
        def __init__(self, subject: str, key_id: str | None, legacy: bool) -> None:
            self.subject = subject
            self.key_id = key_id
            self.legacy = legacy

    assert from_principal(None) == OrderActor(ACTOR_UNKNOWN, None)
    assert from_principal(Principal("deployment", None, True)) == OrderActor(
        ACTOR_HUMAN, "deployment", owner="deployment"
    )
    assert from_principal(Principal("hermes", "key-1", False)) == OrderActor(
        ACTOR_AGENT, "key-1", owner="hermes"
    )
    assert from_principal(Principal("", None, True)) == OrderActor(ACTOR_UNKNOWN, None)
