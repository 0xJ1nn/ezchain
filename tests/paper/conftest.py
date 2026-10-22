"""Fixtures for the paper ledger and order-service tests.

Tests run on an in-memory SQLite database (or a temp file for restart tests). Money
columns store exact decimal strings on SQLite, so reconciliation assertions compare
decimals, not floats. The dev ``ezchain`` Postgres database is never touched.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ezchain_adapters import QuoteRequest, QuoteRouter
from ezchain_api.config import Settings
from ezchain_api.models import Base, PaperOrderRow
from ezchain_core import OrderRequest, PairInfo, Quote, SourceRef, TokenSnapshot, utcnow

# A chain whose ladder is a plain DexScreener mark (no keyless executable quote). The
# executable path is covered by the Solana test below and by the adapter/API suites.
CHAIN = "blast"
TOKEN = "0x0000000000000000000000000000000000000abc"
SYMBOL = "TEST"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "data_mode": "fixture",
        "database_url": "sqlite+pysqlite://",
        "paper_starting_cash_usd": Decimal("10000"),
        "paper_fee_bps": 30,
        "max_order_usd": Decimal("1000"),
        "max_position_usd": Decimal("2500"),
        "max_open_positions": 10,
        "max_daily_loss_usd": Decimal("500"),
        "max_quote_age_seconds": 60,
        "min_liquidity_usd": Decimal("10000"),
        "paper_mark_max_order_usd": Decimal("250"),
    }
    values.update(overrides)
    return Settings(**values)


def make_snapshot(
    *,
    price: Decimal | None = Decimal("2000"),
    liquidity: Decimal | None = Decimal("500000"),
    symbol: str = SYMBOL,
    observed_at: datetime | None = None,
    stale: bool = False,
    pairs: int = 1,
    chain: str = CHAIN,
    address: str = TOKEN,
    pair_created_at: datetime | None = datetime(2020, 1, 1, tzinfo=UTC),
) -> TokenSnapshot:
    observed = observed_at or utcnow()
    pair_list = (
        PairInfo(
            pair_address="0xpair",
            dex="uniswap",
            quote_token="USDC",
            price_usd=price or Decimal(0),
            liquidity_usd=liquidity or Decimal(0),
            volume_24h_usd=Decimal("100000"),
            url=None,
        ),
    ) * pairs
    return TokenSnapshot(
        chain=chain,
        address=address,
        symbol=symbol,
        name=symbol,
        price_usd=price,
        liquidity_usd=liquidity,
        volume_24h_usd=Decimal("100000"),
        price_change_m5=Decimal(0),
        price_change_h1=Decimal("1"),
        price_change_h6=Decimal("2"),
        price_change_h24=Decimal("3"),
        pairs=pair_list,
        source=SourceRef("dexscreener", "https://dexscreener.com", observed),
        stale=stale,
        pair_created_at=pair_created_at,
    )


class FakeDexScreener:
    """Returns the snapshot configured by the test; counts calls to prove caching paths."""

    def __init__(self, snapshot: TokenSnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0

    async def get_token(self, chain: str, address: str) -> TokenSnapshot:
        self.calls += 1
        return self.snapshot

    def set(self, snapshot: TokenSnapshot) -> None:
        self.snapshot = snapshot


class FakeJupiter:
    """Deterministic Jupiter Lite stand-in with a configurable USD price."""

    def __init__(self, price: Decimal = Decimal("2000")) -> None:
        self.price = price
        self.calls: list[tuple[str, Decimal | None, Decimal]] = []

    async def quote_paper(
        self,
        *,
        token_address: str,
        side: str,
        amount_usd: Decimal,
        quantity: Decimal | None = None,
        slippage_bps: int = 50,
    ) -> tuple[Quote, int, int]:
        self.calls.append((side, quantity, amount_usd))
        if side == "buy":
            amount_in = amount_usd
            amount_out = amount_usd / self.price
            token_in, token_out = "USDC", token_address
        else:
            if quantity is None or quantity <= 0:
                from ezchain_adapters import NotFound

                raise NotFound("sell requires a positive token quantity")
            amount_in = quantity
            amount_out = quantity * self.price
            token_in, token_out = token_address, "USDC"
        return (
            Quote(
                chain="solana",
                token_in=token_in,
                token_out=token_out,
                side="buy" if side == "buy" else "sell",
                amount_in=amount_in,
                amount_out=amount_out,
                price=self.price,
                price_impact_pct=Decimal("0.01"),
                fee_usd=Decimal(0),
                kind="jupiter",
                route="Jupiter",
                source=SourceRef("jupiter", "https://jup.ag", utcnow()),
            ),
            6,
            9,
        )


def make_request(
    *,
    side: str = "buy",
    amount_usd: str = "100",
    key: str = "key-00000001",
    chain: str = CHAIN,
    token: str = TOKEN,
    strategy_id: str | None = None,
) -> OrderRequest:
    return OrderRequest(
        chain=chain,
        token_address=token,
        side=side,  # type: ignore[arg-type]
        amount_usd=Decimal(amount_usd),
        idempotency_key=key,
        strategy_id=strategy_id,
    )


class FakeJupiterProvider:
    """Provider-protocol view of ``FakeJupiter`` so the router can dispatch to it."""

    kind = "jupiter"

    def __init__(self, client: FakeJupiter) -> None:
        self._client = client

    def supports(self, chain: Any) -> bool:
        return chain.id == "solana"

    async def quote(self, request: QuoteRequest) -> Quote:
        quote, _, _ = await self._client.quote_paper(
            token_address=request.token_address,
            side=request.side,
            amount_usd=request.amount_usd,
            quantity=request.quantity,
        )
        return quote


def make_router(dex: FakeDexScreener, jupiter: FakeJupiter) -> QuoteRouter:
    return QuoteRouter(providers=[FakeJupiterProvider(jupiter)], dexscreener=dex)  # type: ignore[arg-type]


def place(
    session: Session,
    settings: Settings,
    dex: FakeDexScreener,
    jupiter: FakeJupiter,
    request: OrderRequest,
) -> PaperOrderRow:
    from ezchain_api.services import paper

    return asyncio.run(
        paper.place_order(session, request, settings, dex, make_router(dex, jupiter))  # type: ignore[arg-type]
    )


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker() as db:
        yield db


@pytest.fixture
def dex() -> FakeDexScreener:
    return FakeDexScreener(make_snapshot())


@pytest.fixture
def jupiter() -> FakeJupiter:
    return FakeJupiter()


def fresh_observed(seconds_ago: int = 0) -> datetime:
    return datetime.now(UTC) - timedelta(seconds=seconds_ago)
