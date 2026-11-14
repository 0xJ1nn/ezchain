"""Every bound a trade profile declares refuses the order that breaks it.

The profile table in ``ezchain_core.risk`` declares a minimum, an absolute
maximum, a percent of equity and a per-token cap. The enforcement lives inline
in ``paper.place_order``, where those bounds share two reason codes. These
tests pin each bound to a size that breaks only that bound, so a regression
names which one moved.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ezchain_adapters import QuoteRouter
from ezchain_api.config import Settings, get_settings
from ezchain_api.deps import get_dexscreener, get_quote_router
from ezchain_api.routers import paper as paper_router
from ezchain_api.services import paper
from ezchain_api.services.market_snapshots import record_snapshot
from ezchain_core.risk import TRADE_RISK_PROFILES
from ezchain_core.types import PairInfo, SourceRef, TokenSnapshot

# A chain with no keyless executable quote: only the trade policy can refuse here.
CHAIN = "blast"
TOKEN = "0x0000000000000000000000000000000000000abc"
STARTING_CASH = Decimal("2000")


class FakeDex:
    """A quote good enough to price an entry, so a refusal is the policy's."""

    def __init__(self, price: Decimal = Decimal("2")) -> None:
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
                    self.price,
                    Decimal("500000"),
                    Decimal("1000000"),
                    None,
                ),
            ),
            source=SourceRef("dexscreener", None, observed),
            pair_created_at=datetime(2020, 1, 1, tzinfo=UTC),
        )


def _snapshot(*, price: Decimal) -> TokenSnapshot:
    """A snapshot row the per-token bound can price the held position from."""
    observed = datetime.now(UTC)
    return TokenSnapshot(
        chain=CHAIN,
        address=TOKEN,
        symbol="TEST",
        name="Test",
        price_usd=price,
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
                price,
                Decimal("500000"),
                Decimal("1000000"),
                None,
            ),
        ),
        source=SourceRef("dexscreener", None, observed),
        pair_created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )


def _client(client_for, profile: str) -> TestClient:
    settings = Settings(
        environment="test",
        database_url="sqlite+pysqlite://",
        trade_risk_profile=profile,
        max_order_usd=Decimal("1000"),
        paper_mark_max_order_usd=Decimal("1000"),
    )
    dex = FakeDex()
    router = QuoteRouter(providers=[], dexscreener=dex)  # type: ignore[arg-type]
    return client_for(
        paper_router.router,
        {
            get_settings: lambda: settings,
            get_dexscreener: lambda: dex,
            get_quote_router: lambda: router,
        },
    )


def _order(client: TestClient, *, amount: str, key: str) -> dict:
    response = client.post(
        "/api/v1/paper/orders",
        json={
            "chain": CHAIN,
            "token_address": TOKEN,
            "side": "buy",
            "amount_usd": amount,
            "idempotency_key": f"caps-{key}",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_a_buy_below_the_profile_minimum_is_refused_by_name(client_for, session: Session) -> None:
    """One cent under the minimum is refused, not rounded up to it."""
    below = TRADE_RISK_PROFILES["mid"].min_order_usd - Decimal("0.01")
    body = _order(_client(client_for, "mid"), amount=str(below), key="below-min")
    assert body["status"] == "rejected"
    assert "SIZE_BELOW_MIN" in (body["reason"] or "")


def test_a_buy_at_the_profile_minimum_is_not_refused_for_size(client_for, session: Session) -> None:
    """The minimum is inclusive, so the refusal above is about size and nothing else."""
    at_min = TRADE_RISK_PROFILES["mid"].min_order_usd
    body = _order(_client(client_for, "mid"), amount=str(at_min), key="at-min")
    assert "SIZE_BELOW_MIN" not in (body["reason"] or "")


def test_a_buy_above_the_profile_maximum_is_refused(client_for, session: Session) -> None:
    """The profile's own maximum is the ceiling, not the deployment's $1000."""
    profile = TRADE_RISK_PROFILES["low"]
    body = _order(
        _client(client_for, "low"), amount=str(profile.max_order_usd + 1), key="above-max"
    )
    assert body["status"] == "rejected"
    assert "SIZE_ABOVE_MAX" in (body["reason"] or "")


def test_a_buy_above_percent_of_equity_is_refused(client_for, session: Session) -> None:
    """A size under the absolute maximum still refuses when it breaks the percent cap.

    ``high`` allows $250 or 10% of equity, whichever is smaller. With $2000 of cash
    the percent cap is $200, so a $201 entry is legal in absolute terms and refused
    by the equity bound.
    """
    paper.ensure_genesis(session, STARTING_CASH)
    profile = TRADE_RISK_PROFILES["high"]
    cap = STARTING_CASH * profile.max_order_pct_of_equity
    amount = cap + Decimal("1")
    assert amount < profile.max_order_usd, "the percent bound must be the smaller one"
    body = _order(_client(client_for, "high"), amount=str(amount), key="pct-cap")
    assert body["status"] == "rejected"
    assert "SIZE_ABOVE_MAX" in (body["reason"] or "")


def test_a_second_buy_that_breaks_the_per_token_cap_is_refused(
    client_for, session: Session
) -> None:
    """The per-token cap counts the position already held, so it can refuse later buys.

    ``high`` allows $500 in one token across all entries. Two $200 fills are legal; the
    third takes the position past $500 while staying under the $250 order ceiling and
    the percent bound, so only the per-token cap can refuse it. The held value is
    priced from the recorded mark, so the mark is recorded before the third order.
    """
    client = _client(client_for, "high")
    for index in range(2):
        first = _order(client, amount="200", key=f"token-cap-{index}")
        assert first["status"] == "filled", first
    record_snapshot(session, _snapshot(price=Decimal("2")))
    third = _order(client, amount="200", key="token-cap-over")
    assert third["status"] == "rejected"
    assert "POSITION_LIMIT" in (third["reason"] or "")


def test_the_profiles_get_stricter_as_the_risk_rises() -> None:
    """low < mid < high on every bound, so the profile choice means something."""
    low, mid, high = (TRADE_RISK_PROFILES[n] for n in ("low", "mid", "high"))
    assert low.max_order_usd < mid.max_order_usd < high.max_order_usd
    assert low.max_order_pct_of_equity < mid.max_order_pct_of_equity
    assert mid.max_order_pct_of_equity < high.max_order_pct_of_equity
    assert low.per_token_cap_usd <= mid.per_token_cap_usd <= high.per_token_cap_usd
