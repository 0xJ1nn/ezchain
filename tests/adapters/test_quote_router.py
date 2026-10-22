"""Quote router contract: ladder order, fallbacks, exit requirement, honest marks.

No HTTP: providers are fakes, so the assertions are about routing decisions only.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from ezchain_adapters import (
    NoRoute,
    NotFound,
    QuoteRequest,
    QuoteRouter,
    RateLimited,
    UpstreamError,
)
from ezchain_core import PairInfo, Quote, SourceRef, TokenSnapshot, utcnow

USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


def make_quote(
    request: QuoteRequest,
    *,
    kind: str,
    amount_out: str = "2",
    price: str = "2",
    impact: str | None = "0.001",
    fee: str = "0",
) -> Quote:
    return Quote(
        chain=request.chain.id,
        token_in=request.token_address if request.side == "sell" else USDC,
        token_out=USDC if request.side == "sell" else request.token_address,
        side=request.side,
        amount_in=request.amount_usd if request.side == "buy" else (request.quantity or Decimal(0)),
        amount_out=Decimal(amount_out),
        price=Decimal(price),
        price_impact_pct=None if impact is None else Decimal(impact),
        fee_usd=Decimal(fee),
        kind=kind,  # type: ignore[arg-type]
        route=f"{kind} route",
        source=SourceRef(kind, None, utcnow()),
    )


class FakeProvider:
    """Answers buys, fails sells when asked, or raises a configured error."""

    def __init__(
        self,
        kind: str,
        *,
        error: Exception | None = None,
        fail_sell: bool = False,
        amount_out: str = "2",
        fee: str = "0",
        chains: frozenset[str] | None = None,
    ) -> None:
        self.kind = kind
        self.error = error
        self.fail_sell = fail_sell
        self.amount_out = amount_out
        self.fee = fee
        self.chains = chains
        self.calls: list[QuoteRequest] = []

    def supports(self, chain: Any) -> bool:
        return self.chains is None or chain.id in self.chains

    async def quote(self, request: QuoteRequest) -> Quote:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        if request.side == "sell" and self.fail_sell:
            raise NoRoute("no sell route")
        return make_quote(request, kind=self.kind, amount_out=self.amount_out, fee=self.fee)


class FakeDex:
    def __init__(self, *, price: Decimal | None = Decimal("2")) -> None:
        self.price = price
        self.calls: list[tuple[str, str]] = []

    async def get_token(self, chain: str, address: str) -> TokenSnapshot:
        self.calls.append((chain, address))
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
            pairs=(PairInfo("p", "uniswap", "USDC", self.price or Decimal(0), None, None, None),),
            source=SourceRef("dexscreener", None, utcnow()),
        )


def router(*providers: FakeProvider, dex: FakeDex | None = None) -> QuoteRouter:
    return QuoteRouter(providers=list(providers), dexscreener=dex or FakeDex())  # type: ignore[arg-type]


async def test_falls_back_when_the_first_rung_is_unreachable() -> None:
    kyber = FakeProvider("kyberswap", error=UpstreamError("530"))
    velora = FakeProvider("velora")
    quote = await router(kyber, velora).quote(
        chain="base", token_address="0xtoken", side="buy", amount_usd=Decimal("10")
    )
    assert quote.kind == "velora"
    assert len(kyber.calls) == 1
    # buy + full-size sell check on the venue that answered
    assert [call.side for call in velora.calls] == ["buy", "sell"]


async def test_rate_limited_rung_falls_through() -> None:
    kyber = FakeProvider("kyberswap", error=RateLimited("429"))
    velora = FakeProvider("velora")
    quote = await router(kyber, velora).quote(
        chain="base", token_address="0xtoken", side="buy", amount_usd=Decimal("10")
    )
    assert quote.kind == "velora"


async def test_provider_that_cannot_sell_is_skipped() -> None:
    kyber = FakeProvider("kyberswap", fail_sell=True)
    velora = FakeProvider("velora")
    quote = await router(kyber, velora).quote(
        chain="base", token_address="0xtoken", side="buy", amount_usd=Decimal("10")
    )
    assert quote.kind == "velora"
    assert [call.side for call in kyber.calls] == ["buy", "sell"]


async def test_executable_chain_never_degrades_to_a_mark() -> None:
    dex = FakeDex()
    kyber = FakeProvider("kyberswap", error=UpstreamError("down"))
    velora = FakeProvider("velora", error=UpstreamError("down"))
    lifi = FakeProvider("lifi", error=UpstreamError("down"))
    with pytest.raises(NoRoute):
        await router(kyber, velora, lifi, dex=dex).quote(
            chain="base", token_address="0xtoken", side="buy", amount_usd=Decimal("10")
        )
    # the mark was never consulted: a silent downgrade would book a fake fill
    assert dex.calls == []


async def test_lifi_only_chain_rejects_a_buy_without_an_exit() -> None:
    lifi = FakeProvider("lifi", fail_sell=True)
    with pytest.raises(NoRoute):
        await router(lifi).quote(
            chain="mantle", token_address="0xtoken", side="buy", amount_usd=Decimal("10")
        )


async def test_mark_only_chain_uses_the_dexscreener_mark() -> None:
    dex = FakeDex(price=Decimal("2"))
    quote = await router(dex=dex).quote(
        chain="blast", token_address="0xtoken", side="buy", amount_usd=Decimal("25")
    )
    assert quote.kind == "paper_mark"
    assert quote.amount_out == Decimal("12.5")
    assert quote.price_impact_pct is None
    assert quote.fee_usd == Decimal(0)
    assert dex.calls == [("blast", "0xtoken")]


async def test_mark_only_chain_without_a_price_is_no_route() -> None:
    with pytest.raises(NoRoute):
        await router(dex=FakeDex(price=None)).quote(
            chain="blast", token_address="0xtoken", side="buy", amount_usd=Decimal("25")
        )


async def test_sell_sizes_from_usd_when_quantity_is_missing() -> None:
    kyber = FakeProvider("kyberswap")
    quote = await router(kyber).quote(
        chain="base", token_address="0xtoken", side="sell", amount_usd=Decimal("10")
    )
    assert quote.side == "sell"
    # 10 USD at the 2 USD mark is 5 tokens, quoted as a sell by quantity
    assert kyber.calls[0].quantity == Decimal("5")


async def test_sell_without_a_mark_and_no_quantity_is_no_route() -> None:
    with pytest.raises(NoRoute):
        await router(FakeProvider("kyberswap"), dex=FakeDex(price=None)).quote(
            chain="base", token_address="0xtoken", side="sell", amount_usd=Decimal("10")
        )


async def test_unknown_chain_is_not_found() -> None:
    with pytest.raises(NotFound):
        await router().quote(
            chain="nowhere", token_address="0xtoken", side="buy", amount_usd=Decimal("10")
        )


async def test_zero_buy_amount_is_not_found() -> None:
    with pytest.raises(NotFound):
        await router(FakeProvider("kyberswap")).quote(
            chain="base", token_address="0xtoken", side="buy", amount_usd=Decimal("0")
        )


async def test_provider_that_does_not_cover_the_chain_is_skipped() -> None:
    # KyberSwap is listed on Base's ladder but claims no coverage here; Velora answers.
    kyber = FakeProvider("kyberswap", chains=frozenset({"ethereum"}))
    velora = FakeProvider("velora")
    quote = await router(kyber, velora).quote(
        chain="base", token_address="0xtoken", side="buy", amount_usd=Decimal("10")
    )
    assert quote.kind == "velora"
    assert kyber.calls == []


async def test_zero_amount_out_is_treated_as_no_route() -> None:
    kyber = FakeProvider("kyberswap", amount_out="0")
    velora = FakeProvider("velora")
    quote = await router(kyber, velora).quote(
        chain="base", token_address="0xtoken", side="buy", amount_usd=Decimal("10")
    )
    assert quote.kind == "velora"
