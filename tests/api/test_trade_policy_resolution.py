"""Server-side sizing and the policy decision persisted on orders and runs (GH-107)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_adapters import QuoteRouter
from ezchain_api.config import Settings, get_settings
from ezchain_api.deps import get_dexscreener, get_quote_router
from ezchain_api.models import PaperOrderRow, StrategyRow, StrategyRunRow
from ezchain_api.routers import paper as paper_router
from ezchain_api.services import trade_policy
from ezchain_core.trade_policy import PolicyError
from ezchain_core.types import PairInfo, SourceRef, TokenSnapshot

CHAIN = "blast"
TOKEN = "0x0000000000000000000000000000000000000abc"


class FakeDex:
    async def get_token(self, chain: str, address: str, **_: object) -> TokenSnapshot:
        return TokenSnapshot(
            chain=chain,
            address=address,
            symbol="TEST",
            name="Test",
            price_usd=Decimal("2"),
            liquidity_usd=Decimal("500000"),
            volume_24h_usd=Decimal("1000000"),
            price_change_m5=Decimal(0),
            price_change_h1=Decimal("1"),
            price_change_h6=Decimal(0),
            price_change_h24=Decimal(0),
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
            source=SourceRef("dexscreener", None, datetime.now(UTC)),
            pair_created_at=datetime(2020, 1, 1, tzinfo=UTC),
        )


def _client(client_for, cash: Decimal, profile: str) -> TestClient:
    settings = Settings(
        environment="test",
        data_mode="fixture",
        database_url="sqlite+pysqlite://",
        trade_risk_profile=profile,
        max_order_usd=Decimal("1000"),
        paper_mark_max_order_usd=Decimal("1000"),
        paper_starting_cash_usd=cash,
    )
    dex = FakeDex()
    quotes = QuoteRouter(providers=[], dexscreener=dex)  # type: ignore[arg-type]
    return client_for(
        paper_router.router,
        {
            get_settings: lambda: settings,
            get_dexscreener: lambda: dex,
            get_quote_router: lambda: quotes,
        },
    )


def _post(client: TestClient, key: str, **extra: object):
    return client.post(
        "/api/v1/paper/orders",
        json={
            "chain": CHAIN,
            "token_address": TOKEN,
            "side": "buy",
            "idempotency_key": f"resolve-{key}",
            **extra,
        },
    )


def test_omitted_amount_uses_the_profile_max_when_equity_is_high(
    client_for, session: Session
) -> None:
    """cash of 10000 gives mid a 500 dollar percent cap, so the 100 dollar max wins."""
    response = _post(_client(client_for, Decimal("10000"), "mid"), "max-bound")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "filled"
    assert Decimal(body["amount_usd"]) == Decimal("100")
    assert body["policy"]["resolved_amount_usd"] == "100"
    assert body["policy"]["profile"] == "mid"
    assert body["policy"]["source"] == "manual"
    assert Decimal(body["policy"]["limits"]["max_order_usd"]) == Decimal("100")
    assert body["policy"]["limits"]["min_order_usd"] == "5"


def test_omitted_amount_uses_the_percent_cap_when_it_is_smaller(
    client_for, session: Session
) -> None:
    """cash of 1000 gives high a 100 dollar percent cap, below its 250 dollar max."""
    response = _post(_client(client_for, Decimal("1000"), "high"), "pct-bound")

    assert response.status_code == 201, response.text
    assert Decimal(response.json()["amount_usd"]) == Decimal("100")
    assert Decimal(response.json()["policy"]["limits"]["max_order_pct_of_equity"]) == Decimal(
        "0.10"
    )


def test_the_policy_decision_is_persisted_on_the_order(client_for, session: Session) -> None:
    """The snapshot is stored, not recomputed: it answers what the order was measured against."""
    client = _client(client_for, Decimal("10000"), "mid")
    response = _post(client, "persist")
    assert response.status_code == 201

    row = session.scalar(
        select(PaperOrderRow).where(PaperOrderRow.idempotency_key == "resolve-persist")
    )
    assert row is not None
    assert row.policy_json is not None
    assert row.policy_json["profile"] == "mid"
    assert Decimal(row.policy_json["resolved_amount_usd"]) == Decimal("100")
    assert "max_order_pct_of_equity" in row.policy_json["limits"]


def test_an_unknown_profile_in_a_sized_request_is_a_typed_refusal(
    client_for, session: Session
) -> None:
    client = _client(client_for, Decimal("10000"), "mid")
    response = _post(client, "bad-profile", profile="turbo")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "PROFILE_UNKNOWN"


def test_unknown_equity_refuses_to_size_closed() -> None:
    settings = Settings(environment="test", database_url="sqlite+pysqlite://")
    with pytest.raises(PolicyError) as excinfo:
        trade_policy.resolve_size("mid", None, settings)
    assert excinfo.value.code == "EQUITY_UNKNOWN"


def test_a_profile_change_is_recorded_on_the_strategy_run(client_for, session: Session) -> None:
    strategy = StrategyRow(
        name="sized",
        chain="solana",
        token_address="So11111111111111111111111111111111111111112",
        token_symbol="TEST",
        kind="momentum",
        params={},
        status="active",
    )
    session.add(strategy)
    session.commit()
    settings = Settings(environment="test", database_url="sqlite+pysqlite://")

    trade_policy.update_strategy(
        session, settings, strategy.id, risk_profile="mid", acknowledge_risk=False
    )

    run = session.scalar(select(StrategyRunRow).where(StrategyRunRow.strategy_id == strategy.id))
    assert run is not None
    assert run.policy_json is not None
    assert run.policy_json["profile"] == "mid"
    assert run.policy_json["source"] == "strategy"
    assert Decimal(run.policy_json["limits"]["max_order_usd"]) == Decimal("100")


def test_a_strategy_order_above_its_profile_cap_is_refused_by_name(session) -> None:
    """A strategy's own order_usd never silently buys past the profile maximum."""
    strategy = StrategyRow(
        name="too-big",
        chain="solana",
        token_address="So11111111111111111111111111111111111111112",
        token_symbol="TEST",
        kind="momentum",
        params={"order_usd": "300"},
        status="active",
        risk_profile="mid",
    )
    session.add(strategy)
    session.commit()
    settings = Settings(
        environment="test",
        database_url="sqlite+pysqlite://",
        paper_starting_cash_usd=Decimal("10000"),
        trade_risk_profile="mid",
    )
    from tests.paper.conftest import (
        FakeDexScreener,
        FakeJupiter,
        make_snapshot,
        place,
    )

    from ezchain_core import OrderRequest

    dex = FakeDexScreener(make_snapshot(price=Decimal("2"), liquidity=Decimal("500000")))
    jupiter = FakeJupiter()
    order = place(
        session,
        settings,
        dex,
        jupiter,
        OrderRequest(
            chain=CHAIN,
            token_address=TOKEN,
            side="buy",
            amount_usd=Decimal("150"),
            idempotency_key="resolve-strategy-cap",
            strategy_id=strategy.id,
        ),
    )

    assert order.status == "rejected"
    assert order.reason == "SIZE_ABOVE_MAX"
    warnings = trade_policy.warnings_for(strategy, settings)
    assert "SIZE_ABOVE_MAX" in [item["code"] for item in warnings]
