"""The manual paper route resolves size before a quote and persists the result."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_adapters import QuoteRouter
from ezchain_api.config import Settings
from ezchain_api.deps import get_dexscreener, get_quote_router
from ezchain_api.models import PaperFillRow, PaperOrderRow
from ezchain_api.routers import paper as paper_router
from ezchain_api.services import account_read, paper, trade_policy
from ezchain_core import TradeSizeLimits
from ezchain_core.types import PairInfo, SourceRef, TokenSnapshot

CHAIN = "blast"
TOKEN = "0x0000000000000000000000000000000000000abc"


class FakeDex:
    def __init__(self) -> None:
        self.calls = 0

    async def get_token(self, chain: str, address: str) -> TokenSnapshot:
        self.calls += 1
        observed = datetime.now(UTC)
        return TokenSnapshot(
            chain=chain,
            address=address,
            symbol="TEST",
            name="Test",
            price_usd=Decimal("2"),
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
                    Decimal("2"),
                    Decimal("500000"),
                    Decimal("1000000"),
                    None,
                ),
            ),
            source=SourceRef("dexscreener", None, observed),
            pair_created_at=datetime(2020, 1, 1, tzinfo=UTC),
        )


@pytest.fixture
def dex() -> FakeDex:
    return FakeDex()


@pytest.fixture
def client(client_for, dex: FakeDex) -> TestClient:
    router = QuoteRouter(providers=[], dexscreener=dex)  # type: ignore[arg-type]
    return client_for(
        paper_router.router,
        {get_dexscreener: lambda: dex, get_quote_router: lambda: router},
    )


@pytest.fixture
def genesis(session: Session, settings: Settings) -> None:
    paper.ensure_genesis(session, settings.paper_starting_cash_usd)


def _buy(**extra: object) -> dict[str, object]:
    body: dict[str, object] = {
        "chain": CHAIN,
        "token_address": TOKEN,
        "side": "buy",
        "idempotency_key": "sizing-key-0001",
    }
    body.update(extra)
    return body


def test_omitted_amount_resolves_mid_profile_and_persists_it(
    client: TestClient, session: Session, genesis: None
) -> None:
    response = client.post("/api/v1/paper/orders", json=_buy(risk_profile="mid"))

    assert response.status_code == 201
    body = response.json()
    assert Decimal(body["amount_usd"]) == Decimal("150")
    assert body["risk_profile"] == "mid"
    row = session.scalar(select(PaperOrderRow))
    assert row is not None
    assert row.status == "filled"
    assert row.amount_usd == Decimal("150")
    assert row.risk_profile == "mid"


def test_omitted_amount_and_profile_use_the_policy_default(
    client: TestClient, session: Session, genesis: None
) -> None:
    response = client.post("/api/v1/paper/orders", json=_buy())

    assert response.status_code == 201
    row = session.scalar(select(PaperOrderRow))
    assert row is not None
    assert row.amount_usd == Decimal("150")
    assert row.risk_profile == "mid"


def test_omitted_amount_uses_the_persisted_policy_not_the_config_default(
    client: TestClient, session: Session, settings: Settings, genesis: None
) -> None:
    record = trade_policy.load_trade_policy(session, settings)
    persisted = replace(
        record.policy,
        profiles={
            **record.policy.profiles,
            "mid": TradeSizeLimits(
                min_amount_usd=Decimal("10"),
                min_pct_equity=Decimal("0.10"),
                max_amount_usd=Decimal("200"),
                max_pct_equity=Decimal("2"),
            ),
        },
    )
    trade_policy.save_trade_policy(session, persisted, updated_by="test")

    response = client.post("/api/v1/paper/orders", json=_buy(risk_profile="mid"))

    assert response.status_code == 201
    body = response.json()
    assert Decimal(body["amount_usd"]) == Decimal("200")
    assert body["risk_profile"] == "mid"
    row = session.scalar(select(PaperOrderRow))
    assert row is not None
    assert row.amount_usd == Decimal("200")


def test_unknown_equity_is_rejected_before_any_quote(
    client: TestClient,
    session: Session,
    dex: FakeDex,
    genesis: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(account_read, "equity_usd", lambda _session: None)

    response = client.post("/api/v1/paper/orders", json=_buy(risk_profile="mid"))

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "rejected"
    assert body["reason"] == "EQUITY_UNKNOWN"
    assert body["risk_profile"] == "mid"
    assert dex.calls == 0
    row = session.scalar(select(PaperOrderRow))
    assert row is not None
    assert row.status == "rejected"
    assert row.quote_kind is None
    assert session.scalar(select(PaperFillRow)) is None


def test_manual_amount_is_validated_not_resized(
    client: TestClient, session: Session, dex: FakeDex, genesis: None
) -> None:
    below = client.post(
        "/api/v1/paper/orders",
        json=_buy(amount_usd="5", idempotency_key="sizing-below-min"),
    )
    assert below.status_code == 201
    assert below.json()["status"] == "rejected"
    assert below.json()["reason"] == "SIZE_BELOW_MIN"
    assert dex.calls == 0

    above = client.post(
        "/api/v1/paper/orders",
        json=_buy(amount_usd="150.01", idempotency_key="sizing-above-max"),
    )
    assert above.status_code == 201
    assert above.json()["status"] == "rejected"
    assert above.json()["reason"] == "SIZE_ABOVE_MAX"

    inside = client.post(
        "/api/v1/paper/orders",
        json=_buy(
            amount_usd="42.42",
            risk_profile="low",
            idempotency_key="sizing-inside",
        ),
    )
    assert inside.status_code == 201
    assert inside.json()["status"] == "filled"
    assert Decimal(inside.json()["amount_usd"]) == Decimal("42.42")
    assert inside.json()["risk_profile"] == "low"


def test_reusing_an_idempotency_key_returns_the_first_resolution(
    client: TestClient, session: Session, genesis: None
) -> None:
    first = client.post(
        "/api/v1/paper/orders",
        json=_buy(risk_profile="low", idempotency_key="sizing-idempotent"),
    )
    second = client.post(
        "/api/v1/paper/orders",
        json=_buy(risk_profile="high", idempotency_key="sizing-idempotent"),
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["risk_profile"] == "low"
    assert len(list(session.scalars(select(PaperOrderRow)))) == 1


def test_unknown_profile_is_a_persisted_rejection_without_a_quote(
    client: TestClient, session: Session, dex: FakeDex, genesis: None
) -> None:
    response = client.post(
        "/api/v1/paper/orders", json=_buy(risk_profile="aggressive")
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "rejected"
    assert body["reason"] == "PROFILE_UNKNOWN"
    assert body["risk_profile"] is None
    assert dex.calls == 0
    row = session.scalar(select(PaperOrderRow))
    assert row is not None
    assert row.status == "rejected"
    assert row.risk_profile is None


def test_sell_requires_an_explicit_amount(
    client: TestClient, session: Session, genesis: None
) -> None:
    response = client.post(
        "/api/v1/paper/orders",
        json={
            "chain": CHAIN,
            "token_address": TOKEN,
            "side": "sell",
            "idempotency_key": "sizing-sell-no-amount",
        },
    )

    assert response.status_code == 422
    assert session.scalar(select(PaperOrderRow)) is None
