"""Build a real paper ledger for a rehearsal, through the real order path.

A restore drill is only meaningful if the database it backs up holds rows the system
actually wrote. This module fills a disposable database by calling the same
``ezchain_api.services.paper.place_order`` the REST edge calls: quote, risk, fill and
journal. Only the market read is substituted — a deterministic in-process feed — so the
rehearsal needs no venue credentials and no network.

The scenario is fixed and labelled: two tokens, a buy, an add, a partial reduce-only
exit, a full exit, and one order replayed by its idempotency key so a restore also has to
preserve the replay identity. Nothing here writes to a database it did not create.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from ezchain_adapters import QuoteRouter
from ezchain_api.config import Settings
from ezchain_api.models import PaperFillRow, PaperOrderRow
from ezchain_api.services import paper
from ezchain_core import OrderRequest, PairInfo, SourceRef, TokenSnapshot, utcnow

CHAIN = "blast"
TOKEN_A = "0x00000000000000000000000000000000000a0001"
TOKEN_B = "0x00000000000000000000000000000000000b0002"
PRICE_A = Decimal("2000")
PRICE_B = Decimal("50")

# (side, token, amount_usd, quantity, reduce_only, idempotency_key)
SCENARIO: tuple[tuple[str, str, str, str | None, bool, str], ...] = (
    ("buy", TOKEN_A, "200", None, False, "rehearsal-buy-a-1"),
    ("buy", TOKEN_A, "150", None, False, "rehearsal-buy-a-2"),
    ("buy", TOKEN_B, "120", None, False, "rehearsal-buy-b-1"),
    ("sell", TOKEN_A, "100", "0.05", True, "rehearsal-sell-a-partial"),
    ("buy", TOKEN_B, "90", None, False, "rehearsal-buy-b-2"),
    ("sell", TOKEN_B, "60", "1.2", True, "rehearsal-sell-b-partial"),
    ("sell", TOKEN_A, "250", None, True, "rehearsal-sell-a-rest"),
    # A replay of an existing key: it must return the original order and book no fill.
    ("buy", TOKEN_B, "90", None, False, "rehearsal-buy-b-2"),
)


class FixedFeed:
    """A deterministic market read: one fresh mark per token, no network."""

    def __init__(self) -> None:
        self.calls = 0

    def _snapshot(self, chain: str, address: str) -> TokenSnapshot:
        price = PRICE_A if address == TOKEN_A else PRICE_B
        observed = utcnow()
        return TokenSnapshot(
            chain=chain,
            address=address,
            symbol="TESTA" if address == TOKEN_A else "TESTB",
            name="Rehearsal token",
            price_usd=price,
            liquidity_usd=Decimal("500000"),
            volume_24h_usd=Decimal("100000"),
            price_change_m5=Decimal("0"),
            price_change_h1=Decimal("1"),
            price_change_h6=Decimal("2"),
            price_change_h24=Decimal("3"),
            pairs=(
                PairInfo(
                    pair_address="0xpair",
                    dex="uniswap",
                    quote_token="USDC",
                    price_usd=price,
                    liquidity_usd=Decimal("500000"),
                    volume_24h_usd=Decimal("100000"),
                    url=None,
                ),
            ),
            source=SourceRef("dexscreener", "https://dexscreener.com", observed),
            stale=False,
            pair_created_at=datetime(2020, 1, 1, tzinfo=UTC),
            pair_address="0xpair",
            dex="uniswap",
            base_token_address=address,
            quote_token_address="USDC",
            url=None,
        )

    async def get_token(
        self, chain: str, address: str, *, max_age_seconds: float | None = None
    ) -> TokenSnapshot:
        self.calls += 1
        return self._snapshot(chain, address)


@dataclass(frozen=True)
class SeedResult:
    orders: int
    fills: int
    replayed: int


def _settings(database_url: str) -> Settings:
    return Settings(
        environment="test",
        data_mode="fixture",
        database_url=database_url,
        auto_create_schema=False,
        paper_starting_cash_usd=Decimal("10000"),
        paper_fee_bps=30,
        max_order_usd=Decimal("1000"),
        max_position_usd=Decimal("2500"),
        max_open_positions=10,
        max_daily_loss_usd=Decimal("500"),
        max_quote_age_seconds=60,
        max_mark_age_seconds=60,
        min_liquidity_usd=Decimal("10000"),
        paper_mark_max_order_usd=Decimal("250"),
        engine_kill_switch=False,
    )


def seed(
    database_url: str,
    *,
    scenario: tuple[tuple[str, str, str, str | None, bool, str], ...] = SCENARIO,
) -> SeedResult:
    """Fill an already-migrated database through the real order path."""
    import asyncio

    settings = _settings(database_url)
    engine = create_engine(database_url, future=True)
    feed = FixedFeed()
    router = QuoteRouter(providers=[], dexscreener=feed)  # type: ignore[arg-type]
    seen: set[str] = set()
    replayed = 0
    try:
        maker = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        with maker() as session:
            paper.ensure_genesis(session, settings.paper_starting_cash_usd)
            for side, token, amount, quantity, reduce_only, key in scenario:
                request = OrderRequest(
                    chain=CHAIN,
                    token_address=token,
                    side=side,  # type: ignore[arg-type]
                    amount_usd=Decimal(amount),
                    idempotency_key=key,
                    quantity=None if quantity is None else Decimal(quantity),
                    reduce_only=reduce_only,
                )
                order = asyncio.run(
                    paper.place_order(session, request, settings, feed, router)  # type: ignore[arg-type]
                )
                if order.status != "filled":
                    raise RuntimeError(
                        f"rehearsal order {key!r} did not fill: {order.status} {order.reason}"
                    )
                # A replayed key returns the original row. The order id is the observed
                # evidence, so the count does not depend on any per-call flag.
                if order.id in seen:
                    replayed += 1
                seen.add(order.id)
            session.commit()
            orders = len(list(session.scalars(select(PaperOrderRow.id))))
            fills = len(list(session.scalars(select(PaperFillRow.id))))
    finally:
        engine.dispose()
    if len(seen) != orders:
        raise RuntimeError(f"{len(seen)} distinct orders returned for {orders} stored rows")
    return SeedResult(orders=orders, fills=fills, replayed=replayed)


__all__ = ["SCENARIO", "FixedFeed", "SeedResult", "seed"]
