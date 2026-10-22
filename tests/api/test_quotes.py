"""Quote service contract: Jupiter on Solana, paper mark elsewhere."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from ezchain_adapters import NoRoute
from ezchain_api.config import Settings
from ezchain_api.services.quotes import get_quote
from ezchain_core import PairInfo, Quote, SourceRef, TokenSnapshot, utcnow


class FakeJupiter:
    def __init__(self, *, no_sell: bool = False) -> None:
        self.no_sell = no_sell
        self.calls: list[dict[str, Any]] = []

    async def quote_paper(
        self, *, token_address: str, side: str, amount_usd: Decimal, quantity: Decimal | None = None
    ) -> tuple[Quote, int, int]:
        self.calls.append({"side": side, "quantity": quantity, "amount_usd": amount_usd})
        if side == "sell" and self.no_sell:
            raise NoRoute("no route")
        price = Decimal("0.000003")
        if side == "buy":
            amount_in, amount_out = amount_usd, amount_usd / price
        else:
            amount_in = quantity or Decimal(0)
            amount_out = amount_in * price
        return (
            Quote(
                chain="solana",
                token_in="USDC",
                token_out=token_address,
                side="buy" if side == "buy" else "sell",
                amount_in=amount_in,
                amount_out=amount_out,
                price=price,
                price_impact_pct=Decimal("0"),
                fee_usd=Decimal(0),
                kind="jupiter",
                route="Test",
                source=SourceRef("jupiter", None, utcnow()),
            ),
            6,
            5,
        )


class FakeDex:
    def __init__(self, *, price: Decimal | None = Decimal("2")) -> None:
        self.price = price

    async def get_token(self, chain: str, address: str) -> TokenSnapshot:
        return TokenSnapshot(
            chain=chain,
            address=address,
            symbol="TEST",
            name="Test",
            price_usd=self.price,
            liquidity_usd=Decimal("50000"),
            volume_24h_usd=Decimal("1000"),
            price_change_m5=None,
            price_change_h1=Decimal("1"),
            price_change_h6=None,
            price_change_h24=None,
            pairs=(
                PairInfo(
                    "p",
                    "uniswap",
                    "USDC",
                    self.price or Decimal(0),
                    Decimal("50000"),
                    Decimal("1000"),
                    None,
                ),
            ),
            source=SourceRef("dexscreener", None, utcnow()),
            pair_created_at=datetime(2020, 1, 1, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_solana_buy_checks_a_full_size_sell_route() -> None:
    jupiter = FakeJupiter()
    quote = await get_quote(
        chain="solana",
        token_address="mint",
        side="buy",
        amount_usd=Decimal("10"),
        quantity=None,
        dexscreener=FakeDex(),  # type: ignore[arg-type]
        jupiter=jupiter,  # type: ignore[arg-type]
        settings=Settings(),
    )
    assert quote.kind == "jupiter"
    assert [call["side"] for call in jupiter.calls] == ["buy", "sell"]
    assert jupiter.calls[1]["quantity"] == quote.amount_out


@pytest.mark.asyncio
async def test_solana_buy_without_sell_route_is_rejected() -> None:
    with pytest.raises(NoRoute):
        await get_quote(
            chain="solana",
            token_address="mint",
            side="buy",
            amount_usd=Decimal("10"),
            quantity=None,
            dexscreener=FakeDex(),  # type: ignore[arg-type]
            jupiter=FakeJupiter(no_sell=True),  # type: ignore[arg-type]
            settings=Settings(),
        )


@pytest.mark.asyncio
async def test_non_solana_uses_a_paper_mark() -> None:
    quote = await get_quote(
        chain="base",
        token_address="0xtoken",
        side="buy",
        amount_usd=Decimal("25"),
        quantity=None,
        dexscreener=FakeDex(price=Decimal("2")),  # type: ignore[arg-type]
        jupiter=FakeJupiter(),  # type: ignore[arg-type]
        settings=Settings(),
    )
    assert quote.kind == "paper_mark"
    assert quote.amount_out == Decimal("12.5")


@pytest.mark.asyncio
async def test_non_solana_sell_sizes_from_usd_when_quantity_is_omitted() -> None:
    quote = await get_quote(
        chain="base",
        token_address="0xtoken",
        side="sell",
        amount_usd=Decimal("25"),
        quantity=None,
        dexscreener=FakeDex(price=Decimal("2")),  # type: ignore[arg-type]
        jupiter=FakeJupiter(),  # type: ignore[arg-type]
        settings=Settings(),
    )
    assert quote.amount_in == Decimal("12.5")
    assert quote.amount_out == Decimal("25")


@pytest.mark.asyncio
async def test_solana_sell_sizes_from_usd_when_quantity_is_omitted() -> None:
    jupiter = FakeJupiter()
    quote = await get_quote(
        chain="solana",
        token_address="mint",
        side="sell",
        amount_usd=Decimal("10"),
        quantity=None,
        dexscreener=FakeDex(price=Decimal("2")),  # type: ignore[arg-type]
        jupiter=jupiter,  # type: ignore[arg-type]
        settings=Settings(),
    )
    assert quote.side == "sell"
    assert jupiter.calls[0]["quantity"] == Decimal("5")
