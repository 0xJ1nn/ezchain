"""Quote router contract: ladder order, fallbacks, exit requirement, honest marks.

No HTTP: providers are fakes, so the assertions are about routing decisions only.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest

from ezchain_adapters import (
    JupiterClient,
    NoRoute,
    NotFound,
    ProviderBudgetExhausted,
    ProviderBudgetManager,
    ProviderKeyUnavailable,
    ProviderLimit,
    QuoteRequest,
    QuoteRouter,
    RateLimited,
    UpstreamError,
)
from ezchain_adapters.jupiter import LITE_BASE
from ezchain_core import FreshnessPolicy, PairInfo, Quote, SourceRef, TokenSnapshot, utcnow

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
    def __init__(
        self,
        *,
        price: Decimal | None = Decimal("2"),
        observed_at: datetime | None = None,
    ) -> None:
        self.price = price
        # A test can age the mark to prove the router refuses to price from it.
        self.observed_at = observed_at
        self.calls: list[tuple[str, str]] = []

    async def get_token(
        self, chain: str, address: str, *, max_age_seconds: float | None = None
    ) -> TokenSnapshot:
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
            source=SourceRef("dexscreener", None, self.observed_at or utcnow()),
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


async def test_provider_budget_exhaustion_on_entry_is_not_swallowed() -> None:
    jupiter = FakeProvider(
        "jupiter",
        error=ProviderBudgetExhausted("jupiter", detail="queue is full"),
    )

    with pytest.raises(ProviderBudgetExhausted):
        await router(jupiter).quote(
            chain="solana", token_address="0xtoken", side="buy", amount_usd=Decimal("10")
        )


class BudgetExhaustedOnExit(FakeProvider):
    async def quote(self, request: QuoteRequest) -> Quote:
        if request.side == "sell":
            raise ProviderBudgetExhausted("jupiter", detail="queue is full")
        return await super().quote(request)


async def test_provider_budget_exhaustion_on_exit_is_not_swallowed() -> None:
    jupiter = BudgetExhaustedOnExit("jupiter")

    with pytest.raises(ProviderBudgetExhausted):
        await router(jupiter).quote(
            chain="solana", token_address="0xtoken", side="buy", amount_usd=Decimal("10")
        )


async def test_provider_key_unavailable_is_not_swallowed() -> None:
    jupiter = FakeProvider(
        "jupiter",
        error=ProviderKeyUnavailable("the stored key could not be decrypted"),
    )

    with pytest.raises(ProviderKeyUnavailable):
        await router(jupiter).quote(
            chain="solana", token_address="0xtoken", side="buy", amount_usd=Decimal("10")
        )


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


class SlowProvider(FakeProvider):
    """A provider that never answers inside the router's attempt budget."""

    async def quote(self, request: QuoteRequest) -> Quote:
        import asyncio

        self.calls.append(request)
        await asyncio.sleep(1)
        return make_quote(request, kind=self.kind)


async def test_provider_timeout_fails_closed_with_a_reason(monkeypatch) -> None:
    from ezchain_adapters import quotes as quotes_module

    monkeypatch.setattr(quotes_module, "PROVIDER_ATTEMPT_SECONDS", 0.01)
    monkeypatch.setattr(quotes_module, "QUOTE_DEADLINE_SECONDS", 0.05)
    with pytest.raises(NoRoute) as caught:
        await router(SlowProvider("kyberswap")).quote(
            chain="base", token_address="0xtoken", side="buy", amount_usd=Decimal("10")
        )
    assert "TIMEOUT" in (caught.value.detail or "")
    assert caught.value.code == "NO_ROUTE"


async def test_timeout_tries_the_next_rung(monkeypatch) -> None:
    from ezchain_adapters import quotes as quotes_module

    monkeypatch.setattr(quotes_module, "PROVIDER_ATTEMPT_SECONDS", 0.01)
    monkeypatch.setattr(quotes_module, "QUOTE_DEADLINE_SECONDS", 0.05)
    slow = SlowProvider("kyberswap")
    fallback = FakeProvider("velora")
    quote = await router(slow, fallback).quote(
        chain="base", token_address="0xtoken", side="buy", amount_usd=Decimal("10")
    )
    assert quote.kind == "velora"


class SlowExitProvider(FakeProvider):
    """Answers a buy, then hangs on the mandatory full-size sell check."""

    async def quote(self, request: QuoteRequest) -> Quote:
        import asyncio

        self.calls.append(request)
        if request.side == "buy":
            return make_quote(request, kind=self.kind)
        await asyncio.sleep(1)
        return make_quote(request, kind=self.kind)


async def test_exit_timeout_fails_closed_with_exit_timeout(monkeypatch) -> None:
    from ezchain_adapters import quotes as quotes_module

    monkeypatch.setattr(quotes_module, "PROVIDER_ATTEMPT_SECONDS", 0.01)
    monkeypatch.setattr(quotes_module, "QUOTE_DEADLINE_SECONDS", 0.05)
    with pytest.raises(NoRoute) as caught:
        await router(SlowExitProvider("kyberswap")).quote(
            chain="base", token_address="0xtoken", side="buy", amount_usd=Decimal("10")
        )
    assert "EXIT_TIMEOUT" in (caught.value.detail or "")
    assert caught.value.code == "NO_ROUTE"


SOLANA_MINT = "BudgetDeadlineMint1111111111111111111111111"


async def test_a_competing_wait_is_refused_before_the_attempt_deadline(monkeypatch) -> None:
    """The second HTTP call of one attempt must not outlive the attempt's deadline.

    A slow token lookup plus a competing caller used to leave the budget sleeping past the
    router's ``wait_for``: the budget computed its window from the moment each request
    joined the queue, so the outer timeout fired first and the caller saw a generic
    ``jupiter:TIMEOUT``/NO_ROUTE (409) instead of the typed 429. The attempt owns the
    timeout, so its absolute deadline reaches the budget and the refusal happens first.
    """
    from ezchain_adapters import quotes as quotes_module

    # The attempt is longer than the budget's own window, so only the attempt deadline
    # can make the second acquisition impossible. Shrinking both keeps the test fast.
    monkeypatch.setattr(quotes_module, "PROVIDER_ATTEMPT_SECONDS", 0.8)
    manager = ProviderBudgetManager(
        limits=[ProviderLimit("jupiter", 2.0, 1, 4, 0.6)], clock=time.monotonic
    )
    search_started = asyncio.Event()
    release_search = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tokens/v2/search":
            search_started.set()
            await release_search.wait()
            return httpx.Response(200, json=[{"id": SOLANA_MINT, "decimals": 6}])
        return httpx.Response(200, json={"outAmount": "1000000", "routePlan": []})

    async with httpx.AsyncClient(
        base_url=LITE_BASE, transport=httpx.MockTransport(handler)
    ) as http_client:
        jupiter = JupiterClient(client=http_client, budget=manager)
        attempt = asyncio.create_task(
            router(jupiter).quote(
                chain="solana",
                token_address=SOLANA_MINT,
                side="buy",
                amount_usd=Decimal("1"),
            )
        )
        await search_started.wait()
        # A competitor takes the next slot while the first lookup is still in flight.
        # Its own wait fits inside the budget window; the attempt is what runs out.
        await manager.acquire("jupiter")
        release_search.set()

        with pytest.raises(ProviderBudgetExhausted) as caught:
            await attempt

    assert caught.value.code == "PROVIDER_BUDGET_EXHAUSTED"
    assert caught.value.http_status == 429


async def test_a_stale_mark_refuses_to_price() -> None:
    """A mark past its budget is not current: the router refuses instead of pricing."""
    stale = utcnow() - timedelta(seconds=120)
    with pytest.raises(NoRoute) as caught:
        await router(dex=FakeDex(price=Decimal("2"), observed_at=stale)).quote(
            chain="blast", token_address="0xtoken", side="buy", amount_usd=Decimal("25")
        )
    assert caught.value.code == "SNAPSHOT_STALE"


async def test_a_stale_mark_refuses_to_size_a_sell() -> None:
    """Sizing from a stale mark is pricing from it, so a sell without a quantity refuses."""
    stale = utcnow() - timedelta(seconds=120)
    with pytest.raises(NoRoute):
        await router(FakeProvider("kyberswap"), dex=FakeDex(observed_at=stale)).quote(
            chain="base", token_address="0xtoken", side="sell", amount_usd=Decimal("10")
        )


async def test_a_fresh_mark_still_prices() -> None:
    quote = await router(dex=FakeDex(price=Decimal("2"), observed_at=utcnow())).quote(
        chain="blast", token_address="0xtoken", side="buy", amount_usd=Decimal("25")
    )
    assert quote.kind == "paper_mark"
    assert quote.amount_out == Decimal("12.5")


async def test_the_mark_budget_is_configurable() -> None:
    aged = utcnow() - timedelta(seconds=45)
    strict = QuoteRouter(
        providers=[],
        dexscreener=FakeDex(price=Decimal("2"), observed_at=aged),  # type: ignore[arg-type]
        freshness=FreshnessPolicy(max_age_by_class={"mark": 30.0}),
    )
    with pytest.raises(NoRoute):
        await strict.quote(
            chain="blast", token_address="0xtoken", side="buy", amount_usd=Decimal("25")
        )
    lenient = QuoteRouter(
        providers=[],
        dexscreener=FakeDex(price=Decimal("2"), observed_at=aged),  # type: ignore[arg-type]
        freshness=FreshnessPolicy(max_age_by_class={"mark": 60.0}),
    )
    quote = await lenient.quote(
        chain="blast", token_address="0xtoken", side="buy", amount_usd=Decimal("25")
    )
    assert quote.kind == "paper_mark"


async def test_a_stale_mark_alerts_once_per_transition() -> None:
    stale = utcnow() - timedelta(seconds=120)
    router_ = router(dex=FakeDex(price=Decimal("2"), observed_at=stale))
    for _ in range(3):
        with pytest.raises(NoRoute):
            await router_.quote(
                chain="blast", token_address="0xtoken", side="buy", amount_usd=Decimal("25")
            )
    alerts = router_.stale_alerts()
    assert len(alerts) == 1
    assert alerts[0].type == "provider_stale"
    assert alerts[0].entity == "mark:blast:0xtoken"
    # Drained once; a repeat read of the same outage raises nothing new.
    assert router_.stale_alerts() == ()
    with pytest.raises(NoRoute):
        await router_.quote(
            chain="blast", token_address="0xtoken", side="buy", amount_usd=Decimal("25")
        )
    assert router_.stale_alerts() == ()


async def test_recovery_lets_the_next_outage_alert_again() -> None:
    stale = utcnow() - timedelta(seconds=120)
    dex = FakeDex(price=Decimal("2"), observed_at=stale)
    router_ = router(dex=dex)
    with pytest.raises(NoRoute):
        await router_.quote(
            chain="blast", token_address="0xtoken", side="buy", amount_usd=Decimal("25")
        )
    assert len(router_.stale_alerts()) == 1
    dex.observed_at = utcnow()
    quote = await router_.quote(
        chain="blast", token_address="0xtoken", side="buy", amount_usd=Decimal("25")
    )
    assert quote.kind == "paper_mark"
    dex.observed_at = stale
    with pytest.raises(NoRoute):
        await router_.quote(
            chain="blast", token_address="0xtoken", side="buy", amount_usd=Decimal("25")
        )
    assert len(router_.stale_alerts()) == 1


async def test_a_future_dated_mark_is_left_to_the_risk_gate() -> None:
    """A future mark is a clock fault, named by the risk gate, not a stale-mark refusal."""
    ahead = utcnow() + timedelta(hours=1)
    quote = await router(dex=FakeDex(price=Decimal("2"), observed_at=ahead)).quote(
        chain="blast", token_address="0xtoken", side="buy", amount_usd=Decimal("25")
    )
    assert quote.kind == "paper_mark"


async def test_two_outages_with_a_recovery_between_raise_two_alerts() -> None:
    """A successful sizing must be observed, or a recovery never closes the outage.

    The router only recorded a mark read when it refused one, so after a first outage a
    later *successful* sizing left the tracker believing the source was still stale and
    the second outage raised nothing. Each transition now raises exactly once.
    """
    stale = utcnow() - timedelta(seconds=120)
    dex = FakeDex(price=Decimal("2"), observed_at=stale)
    router_ = router(FakeProvider("kyberswap"), dex=dex)

    # Outage one: sizing refuses and records the transition.
    with pytest.raises(NoRoute):
        await router_.quote(
            chain="base", token_address="0xtoken", side="sell", amount_usd=Decimal("10")
        )
    assert len(router_.stale_alerts()) == 1

    # Recovery: sizing succeeds, and that success is what closes the outage.
    dex.observed_at = utcnow()
    quote = await router_.quote(
        chain="base", token_address="0xtoken", side="sell", amount_usd=Decimal("10")
    )
    assert quote.kind == "kyberswap"
    assert router_.stale_alerts() == ()

    # Outage two: a fresh->stale edge again, so it raises again.
    dex.observed_at = stale
    with pytest.raises(NoRoute):
        await router_.quote(
            chain="base", token_address="0xtoken", side="sell", amount_usd=Decimal("10")
        )
    assert len(router_.stale_alerts()) == 1
