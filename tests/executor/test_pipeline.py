"""Acceptance tests for the single execution pipeline.

Every test drives the same :class:`ExecutionPipeline` a paper, testnet or live deployment
runs; only the venue behind the protocol changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import timedelta
from decimal import Decimal

import pytest

from ezchain_core import (
    Intent,
    LedgerEntry,
    PairInfo,
    PositionState,
    RiskLimits,
    SourceRef,
    TokenSnapshot,
    utcnow,
)
from ezchain_executor import (
    ExecutionPipeline,
    FakeVenue,
    LocalTransactionSigner,
    MemoryJournal,
    SigningBoundary,
)
from ezchain_executor.kill_switch import KillSwitch
from ezchain_executor.pipeline import IntentRejected

CHAIN = "solana"
TOKEN = "Mint1111111111111111111111111111111111111111"


def buy(**overrides: object) -> Intent:
    values: dict[str, object] = {
        "side": "buy",
        "amount_usd": Decimal("100"),
        "chain": CHAIN,
        "token_address": TOKEN,
        "idempotency_key": "intent-1",
    }
    values.update(overrides)
    return Intent(**values)  # type: ignore[arg-type]


def sell(**overrides: object) -> Intent:
    values: dict[str, object] = {
        "side": "sell",
        "amount_usd": Decimal("0"),
        "chain": CHAIN,
        "token_address": TOKEN,
        "idempotency_key": "exit-1",
        "quantity": Decimal("10"),
        "reduce_only": True,
    }
    values.update(overrides)
    return Intent(**values)  # type: ignore[arg-type]


def make_snapshot(*, price: str | None = "2", stale: bool = False, now=None) -> TokenSnapshot:
    observed = now or utcnow()
    return TokenSnapshot(
        chain=CHAIN,
        address=TOKEN,
        symbol="TEST",
        name="Test",
        price_usd=None if price is None else Decimal(price),
        liquidity_usd=Decimal("500000"),
        volume_24h_usd=Decimal("1000000"),
        price_change_m5=Decimal("0"),
        price_change_h1=Decimal("1"),
        price_change_h6=Decimal("0"),
        price_change_h24=Decimal("0"),
        pairs=(
            PairInfo(
                pair_address="pair-1",
                dex="raydium",
                quote_token="USDC",
                price_usd=Decimal(price or "2"),
                liquidity_usd=Decimal("500000"),
                volume_24h_usd=Decimal("1000000"),
                url=None,
            ),
        ),
        source=SourceRef("dexscreener", None, observed),
        stale=stale,
        pair_created_at=observed - timedelta(days=1),
    )


def limits() -> RiskLimits:
    return RiskLimits(
        max_order_usd=Decimal("1000"),
        max_position_usd=Decimal("2500"),
        max_open_positions=10,
        max_daily_loss_usd=Decimal("500"),
        max_quote_age_seconds=60,
        min_liquidity_usd=Decimal("10000"),
        paper_mark_max_order_usd=Decimal("250"),
        min_pair_age_minutes=10,
        max_price_impact_pct=Decimal("0.5"),
    )


def position(quantity: str = "0", cost: str = "0") -> PositionState:
    return PositionState(
        chain=CHAIN,
        token_address=TOKEN,
        symbol="TEST",
        quantity=Decimal(quantity),
        cost_usd=Decimal(cost),
    )


def pipeline(
    venue: FakeVenue | None = None,
    *,
    journal: MemoryJournal | None = None,
    kill_switch: KillSwitch | None = None,
) -> tuple[ExecutionPipeline, FakeVenue, MemoryJournal]:
    venue = venue or FakeVenue()
    journal = journal or MemoryJournal()
    return (
        ExecutionPipeline(
            venue=venue,
            sink=journal,
            limits=limits(),
            kill_switch=kill_switch or KillSwitch(),
            timeout_seconds=0.2,
        ),
        venue,
        journal,
    )


# --- One path, every stage ---------------------------------------------------


@pytest.mark.asyncio
async def test_a_fill_walks_every_stage_and_journals_the_receipt() -> None:
    pipe, venue, journal = pipeline(FakeVenue(fee_usd=Decimal("1")))
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "filled"
    assert result.risk_decision.allowed
    assert result.quote is not None
    assert result.order is not None
    assert result.fill is not None
    assert result.journal is not None
    assert venue.quote_calls == 1
    assert venue.submit_calls == 1

    # Quantity and notional come from the quote, never from the intent's USD request.
    assert result.order.quantity == result.quote.amount_out
    assert result.fill.quantity == result.quote.amount_out
    assert result.fill.notional_usd == Decimal("100")

    # The ledger saw the receipt, through the sink, and only there.
    assert journal.groups == (f"fill:{result.order.id}",)
    assert journal.entries == result.journal.entries
    assert _sum_usd(journal.entries) == Decimal("0")


@pytest.mark.asyncio
async def test_a_partial_fill_with_a_repeating_decimal_notional_is_journaled() -> None:
    """A partial fill's notional is a division result, and the receipt is a computed value.

    ``FakeVenue(mode="partial", partial_ratio=1/3)`` fills ``50 * (1/3)`` tokens at $2,
    so the receipt's notional is ``33.33333333333333333333333332`` — off the ledger's
    minor unit by more than a rounding error, because the venue's own division already
    ran out of digits. The boundary quantizes it half-even exactly once, so the fill is
    journaled rather than raising ``SubQuantumError`` out of the pipeline while the money
    has already moved at the venue.
    """
    venue = FakeVenue(mode="partial", partial_ratio=Decimal(1) / Decimal(3))
    pipe, _, journal = pipeline(venue)
    result = await pipe.run(buy(amount_usd=Decimal("100")), snapshot=make_snapshot())

    assert result.status == "partially_filled"
    assert result.fill is not None
    assert result.journal is not None
    assert result.fill.notional_usd == Decimal("33.33333333333333333333333332")
    assert journal.groups == (f"fill:{result.order.id}",)
    position_leg = next(
        entry for entry in result.journal.entries if entry.account.startswith("position:")
    )
    assert position_leg.usd_value == Decimal("33.33333333")
    assert position_leg.amount == result.fill.quantity
    assert _sum_usd(result.journal.entries) == Decimal("0")


@pytest.mark.asyncio
async def test_a_confirmed_fill_is_booked_from_the_receipt_not_the_intent() -> None:
    # The venue confirms a smaller fill than the intent asked for; the ledger must agree
    # with the receipt, not with the request.
    venue = FakeVenue(mode="partial", partial_ratio=Decimal("0.25"))
    pipe, _, journal = pipeline(venue)
    result = await pipe.run(buy(amount_usd=Decimal("100")), snapshot=make_snapshot())

    assert result.status == "partially_filled"
    assert result.fill is not None
    assert result.journal is not None
    assert result.fill.quantity == Decimal("12.5")  # 100 / 2 * 0.25
    assert result.fill.notional_usd == Decimal("25")
    booked = journal.entries
    assert booked[0].amount == Decimal("12.5")
    assert booked[0].account == f"position:{CHAIN}:{TOKEN}"


# --- Failure paths -----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_venue_rejection_produces_no_journal() -> None:
    pipe, venue, journal = pipeline(FakeVenue(mode="reject"))
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "rejected"
    assert result.reason == "VENUE_REJECTED"
    assert result.fill is None
    assert result.journal is None
    assert journal.groups == ()
    assert venue.submit_calls == 1


@pytest.mark.asyncio
async def test_a_submit_timeout_produces_no_journal() -> None:
    pipe, venue, journal = pipeline(FakeVenue(mode="timeout", timeout_seconds=5))
    result = await pipe.run(buy(), snapshot=make_snapshot(), timeout_seconds=0.01)

    assert result.status == "timeout"
    assert result.reason == "TIMEOUT"
    assert result.order is not None
    assert result.journal is None
    assert journal.groups == ()
    assert venue.submit_calls == 1


@pytest.mark.asyncio
async def test_a_missing_quote_produces_no_order() -> None:
    pipe, venue, journal = pipeline(FakeVenue(mode="no_quote"))
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "rejected"
    assert result.reason == "NO_QUOTE"
    assert result.order is None
    assert venue.submit_calls == 0
    assert journal.groups == ()


@pytest.mark.asyncio
async def test_an_unbookable_receipt_is_refused() -> None:
    class ZeroFillVenue(FakeVenue):
        async def submit(self, order, signed=None):  # type: ignore[no-untyped-def]
            from ezchain_core import Fill

            await super().submit(order, signed)
            return Fill(
                order_id=order.id,
                side=order.side,
                quantity=Decimal("0"),
                price_usd=order.quote.price,
                notional_usd=Decimal("0"),
                fee_usd=Decimal("0"),
            )

    pipe, _, journal = pipeline(ZeroFillVenue())
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "rejected"
    assert result.reason == "UNBOOKABLE_RECEIPT"
    assert journal.groups == ()


# --- Fail closed on money ----------------------------------------------------


@pytest.mark.asyncio
async def test_an_unknown_buy_mark_is_denied_before_the_venue() -> None:
    pipe, venue, journal = pipeline()
    result = await pipe.run(buy(), snapshot=make_snapshot(price=None))

    assert result.status == "denied"
    assert "PRICE_UNKNOWN" in (result.reason or "")
    assert venue.quote_calls == 0
    assert journal.groups == ()


@pytest.mark.asyncio
async def test_a_missing_snapshot_denies_a_buy() -> None:
    pipe, venue, journal = pipeline()
    result = await pipe.run(buy(), snapshot=None)

    assert result.status == "denied"
    assert result.reason == "MISSING_RISK_INPUTS"
    assert venue.quote_calls == 0
    assert journal.groups == ()


@pytest.mark.asyncio
async def test_a_stale_quote_is_denied_after_the_quote_stage() -> None:
    quote_time = utcnow() - timedelta(minutes=5)
    pipe, venue, journal = pipeline(FakeVenue(quote_observed_at=quote_time))
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "denied"
    assert "QUOTE_STALE" in (result.reason or "")
    assert result.quote is not None
    assert result.order is None
    assert venue.submit_calls == 0
    assert journal.groups == ()


# --- Duplicate intents -------------------------------------------------------


@pytest.mark.asyncio
async def test_a_duplicate_intent_yields_one_order_and_one_fill() -> None:
    pipe, venue, journal = pipeline(FakeVenue(mode="fill"))
    intent = buy(idempotency_key="dup-1")

    first = await pipe.run(intent, snapshot=make_snapshot())
    second = await pipe.run(intent, snapshot=make_snapshot())

    assert first.status == "filled"
    assert second.duplicate is True
    assert second.order is not None and first.order is not None
    assert second.order.id == first.order.id
    assert venue.submit_calls == 1
    assert len(journal.groups) == 1


@pytest.mark.asyncio
async def test_a_concurrent_duplicate_waits_for_the_first_fill() -> None:
    import asyncio

    pipe, venue, journal = pipeline(FakeVenue())
    intent = buy(idempotency_key="dup-concurrent")
    results = await asyncio.gather(
        pipe.run(intent, snapshot=make_snapshot()),
        pipe.run(intent, snapshot=make_snapshot()),
    )

    assert venue.submit_calls == 1
    assert sum(1 for r in results if r.duplicate) == 1
    assert len(journal.groups) == 1
    assert {r.order.id for r in results if r.order is not None}  # one order id


# --- Reduce-only exits -------------------------------------------------------


@pytest.mark.asyncio
async def test_a_reduce_only_exit_ignores_entry_gates() -> None:
    # Liquidity is unknown, which blocks new risk, but the exit is still executable.
    snapshot = TokenSnapshot(
        chain=CHAIN,
        address=TOKEN,
        symbol="TEST",
        name="Test",
        price_usd=Decimal("2"),
        liquidity_usd=None,
        volume_24h_usd=None,
        price_change_m5=None,
        price_change_h1=None,
        price_change_h6=None,
        price_change_h24=None,
        source=SourceRef("dexscreener", None, utcnow()),
        stale=False,
    )
    pipe, venue, journal = pipeline()
    result = await pipe.run(sell(), snapshot=snapshot, position=position("10", "20"))

    assert result.status == "filled"
    assert result.fill is not None
    assert result.fill.quantity == Decimal("10")
    assert result.order is not None
    assert result.order.reduce_only is True
    assert journal.groups != ()


@pytest.mark.asyncio
async def test_a_reduce_only_exit_is_sized_to_the_position_not_a_cap() -> None:
    # The position is far larger than any order cap; the exit still closes all of it.
    pipe, _, journal = pipeline()
    result = await pipe.run(
        Intent(
            side="sell",
            amount_usd=Decimal("1"),
            chain=CHAIN,
            token_address=TOKEN,
            idempotency_key="exit-large",
            reduce_only=True,
        ),
        snapshot=make_snapshot(price="2"),
        position=position("1000", "2000"),
    )

    assert result.status == "filled"
    assert result.fill is not None
    assert result.fill.quantity == Decimal("1000")
    booked = {entry.account: entry for entry in journal.entries}
    assert booked[f"position:{CHAIN}:{TOKEN}"].amount == Decimal("-1000")


@pytest.mark.asyncio
async def test_a_sell_never_exceeds_the_position() -> None:
    pipe, venue, _ = pipeline()
    result = await pipe.run(
        sell(quantity=Decimal("50")), snapshot=make_snapshot(), position=position("10", "20")
    )

    assert result.status == "filled"
    assert result.order is not None
    assert venue.submitted[0].quantity == Decimal("10")
    assert result.fill is not None
    assert result.fill.quantity == Decimal("10")


@pytest.mark.asyncio
async def test_a_sell_without_a_position_is_rejected() -> None:
    pipe, venue, journal = pipeline()
    result = await pipe.run(sell(), snapshot=make_snapshot(), position=position("0", "0"))

    assert result.status == "rejected"
    assert result.reason == "INSUFFICIENT_POSITION"
    assert venue.quote_calls == 0
    assert journal.groups == ()


@pytest.mark.asyncio
async def test_a_sell_without_a_mark_and_no_quantity_fails_closed() -> None:
    pipe, venue, _ = pipeline()
    result = await pipe.run(
        Intent(
            side="sell",
            amount_usd=Decimal("100"),
            chain=CHAIN,
            token_address=TOKEN,
            idempotency_key="exit-no-mark",
        ),
        snapshot=make_snapshot(price=None),
        position=position("10", "20"),
    )

    assert result.status == "rejected"
    assert result.reason == "PRICE_UNKNOWN"
    assert venue.quote_calls == 0


# --- Kill switch -------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_kill_switch_stops_execution_before_the_venue() -> None:
    armed = KillSwitch(read=lambda: True)
    pipe, venue, journal = pipeline(kill_switch=armed)
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "denied"
    assert result.reason == "KILL_SWITCH_ACTIVE"
    assert venue.quote_calls == 0
    assert journal.groups == ()


@pytest.mark.asyncio
async def test_an_unreadable_kill_switch_fails_closed() -> None:
    def unavailable() -> bool:
        raise RuntimeError("database down")

    pipe, venue, journal = pipeline(kill_switch=KillSwitch(read=unavailable))
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "denied"
    assert result.reason == "KILL_SWITCH_UNAVAILABLE"
    assert venue.quote_calls == 0
    assert journal.groups == ()


@pytest.mark.asyncio
async def test_the_kill_switch_is_read_on_every_intent() -> None:
    state = {"armed": False}
    pipe, _, _ = pipeline(kill_switch=KillSwitch(read=lambda: state["armed"]))
    first = await pipe.run(buy(idempotency_key="a"), snapshot=make_snapshot())
    state["armed"] = True
    second = await pipe.run(buy(idempotency_key="b"), snapshot=make_snapshot())

    assert first.status == "filled"
    assert second.status == "denied"
    assert second.reason == "KILL_SWITCH_ACTIVE"


# --- Ledger is the only accountant ------------------------------------------


@pytest.mark.asyncio
async def test_the_pipeline_never_writes_the_ledger_directly() -> None:
    # Every entry reached the journal through the sink, in balanced groups, and the
    # pipeline exposes no balance: cash is a replay of these rows.
    pipe, _, journal = pipeline(FakeVenue(fee_usd=Decimal("0.5")))
    await pipe.run(buy(idempotency_key="buy-1"), snapshot=make_snapshot())
    await pipe.run(
        sell(idempotency_key="sell-1"),
        snapshot=make_snapshot(),
        position=position("50", "100"),
    )

    assert len(journal.groups) == 2
    for group in journal.groups:
        assert _sum_usd(journal.entries_for(group)) == Decimal("0")
    cash = sum(
        (entry.usd_value for entry in journal.entries if entry.account == "cash:USD"),
        Decimal(0),
    )
    assert cash != Decimal(0)


@pytest.mark.asyncio
async def test_a_journal_failure_is_reported_and_not_hidden() -> None:
    class BrokenSink:
        def append(self, journal):  # type: ignore[no-untyped-def]
            raise RuntimeError("disk full")

    pipe = ExecutionPipeline(
        venue=FakeVenue(), sink=BrokenSink(), limits=limits(), timeout_seconds=0.2
    )
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "journal_error"
    assert result.fill is not None
    assert "JOURNAL_FAILED" in (result.reason or "")


@pytest.mark.asyncio
async def test_a_journal_that_cannot_be_built_is_a_journal_error_not_a_crash() -> None:
    """Building the journal sits inside the same guard as appending it.

    The venue confirmed a receipt this process cannot book: the fill is accepted and
    priced, but its notional quantizes to zero, so the journal refuses it. The money moved,
    so the caller must get ``journal_error`` with the fill attached — never an exception
    with the fill lost.
    """

    class UnbookableReceiptVenue(FakeVenue):
        async def submit(self, order, signed=None):  # type: ignore[no-untyped-def]
            fill = await super().submit(order, signed)
            return replace(fill, notional_usd=Decimal("0.000000001"))

    pipe, _, journal = pipeline(UnbookableReceiptVenue())
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "journal_error"
    assert result.fill is not None
    assert result.journal is None
    assert "JOURNAL_FAILED" in (result.reason or "")
    assert journal.groups == ()


@pytest.mark.asyncio
async def test_place_raises_for_a_refused_intent() -> None:
    pipe, _, _ = pipeline(FakeVenue(mode="reject"))
    with pytest.raises(IntentRejected) as caught:
        await pipe.place(buy(), snapshot=make_snapshot())
    assert caught.value.result.status == "rejected"


# --- Money-path regressions (review probes) --------------------------------


@dataclass
class InflatedQuoteVenue(FakeVenue):
    """Quotes a notional far larger than the intent's risk-approved size."""

    inflated_in: Decimal = Decimal("5000")

    async def quote(self, intent):  # type: ignore[no-untyped-def]
        quote = await super().quote(intent)
        return replace(
            quote,
            amount_in=self.inflated_in,
            amount_out=self.inflated_in / self.price_usd,
        )


@dataclass
class OverSizedSellVenue(FakeVenue):
    """Quotes more tokens than the intent was sized to sell."""

    async def quote(self, intent):  # type: ignore[no-untyped-def]
        quote = await super().quote(intent)
        return replace(
            quote,
            amount_in=Decimal("50"),
            amount_out=Decimal("50") * self.price_usd,
        )


@dataclass
class ArmingVenue(FakeVenue):
    """Arms the operator switch while the quote round-trip is in flight."""

    state: dict[str, bool] = field(default_factory=lambda: {"armed": False})

    async def quote(self, intent):  # type: ignore[no-untyped-def]
        quote = await super().quote(intent)
        self.state["armed"] = True
        return quote


@dataclass
class ArmingKeyRing:
    """Arms the operator switch while the signed transaction is being produced."""

    state: dict[str, bool] = field(default_factory=lambda: {"armed": False})
    calls: int = 0

    def sign(self, payload: bytes) -> bytes:
        self.calls += 1
        self.state["armed"] = True
        return b"test-signature"


@pytest.mark.asyncio
async def test_a_stale_quote_is_refused_for_a_sell_with_no_snapshot() -> None:
    # The re-risk stage must consult the quote for an exit as well: a missing snapshot
    # cannot turn the quote's freshness gate into a no-op.
    quote_time = utcnow() - timedelta(minutes=30)
    pipe, venue, journal = pipeline(FakeVenue(quote_observed_at=quote_time))
    result = await pipe.run(sell(), snapshot=None, position=position("10", "20"))

    assert result.status == "denied"
    assert venue.quote_calls == 1
    assert result.order is None
    assert venue.submit_calls == 0
    assert journal.groups == ()


@pytest.mark.asyncio
async def test_a_stale_quote_is_refused_for_a_sell_with_a_snapshot() -> None:
    quote_time = utcnow() - timedelta(minutes=30)
    pipe, venue, journal = pipeline(FakeVenue(quote_observed_at=quote_time))
    result = await pipe.run(sell(), snapshot=make_snapshot(), position=position("10", "20"))

    assert result.status == "denied"
    assert "QUOTE_STALE" in (result.reason or "")
    assert venue.submit_calls == 0
    assert journal.groups == ()


@pytest.mark.asyncio
async def test_an_order_is_refused_when_the_quote_exceeds_the_approved_size() -> None:
    # Risk approved $100; the venue quotes $5000. The order must not be priced from the
    # quote when the quote is not the approved size.
    pipe, venue, journal = pipeline(InflatedQuoteVenue())
    result = await pipe.run(buy(amount_usd=Decimal("100")), snapshot=make_snapshot())

    assert result.status == "rejected"
    assert result.reason == "QUOTE_SIZE_MISMATCH"
    assert result.order is None
    assert venue.submit_calls == 0
    assert journal.groups == ()


@pytest.mark.asyncio
async def test_a_sell_is_refused_when_the_quote_exceeds_the_approved_quantity() -> None:
    pipe, venue, journal = pipeline(OverSizedSellVenue())
    result = await pipe.run(
        sell(quantity=Decimal("10")), snapshot=make_snapshot(), position=position("10", "20")
    )

    assert result.status == "rejected"
    assert result.reason == "QUOTE_SIZE_MISMATCH"
    assert venue.submit_calls == 0
    assert journal.groups == ()


@pytest.mark.asyncio
async def test_a_sell_is_rebounded_to_the_held_position_before_submit() -> None:
    # The ledger shrinks during the quote round-trip: the submit must be refused, not
    # booked against a position that is no longer held.
    reads = {"count": 0}

    async def source(intent):  # type: ignore[no-untyped-def]
        reads["count"] += 1
        return position("10", "20") if reads["count"] == 1 else position("5", "10")

    pipe, venue, journal = pipeline()
    pipe.position_source = source
    result = await pipe.run(sell(quantity=Decimal("10")), snapshot=make_snapshot())

    assert result.status == "rejected"
    assert result.reason == "POSITION_CHANGED"
    assert venue.submit_calls == 0
    assert journal.groups == ()


@pytest.mark.asyncio
async def test_a_reused_key_with_a_different_payload_is_a_conflict() -> None:
    pipe, venue, journal = pipeline()
    first = await pipe.run(
        buy(idempotency_key="dup-diff", amount_usd=Decimal("100")), snapshot=make_snapshot()
    )
    second = await pipe.run(
        buy(idempotency_key="dup-diff", amount_usd=Decimal("900")), snapshot=make_snapshot()
    )

    assert first.status == "filled"
    assert second.status == "rejected"
    assert second.reason == "IDEMPOTENCY_CONFLICT"
    assert second.duplicate is False
    assert venue.submit_calls == 1
    assert len(journal.groups) == 1


@pytest.mark.asyncio
async def test_a_denial_is_not_cached_so_a_fixed_retry_runs() -> None:
    pipe, venue, journal = pipeline()
    intent = buy(idempotency_key="retry-denied")

    first = await pipe.run(intent, snapshot=None)
    assert first.status == "denied"
    assert first.reason == "MISSING_RISK_INPUTS"

    second = await pipe.run(intent, snapshot=make_snapshot())
    assert second.status == "filled"
    assert second.duplicate is False
    assert venue.quote_calls == 1
    assert venue.submit_calls == 1
    assert len(journal.groups) == 1


@pytest.mark.asyncio
async def test_the_idempotency_store_is_bounded_and_evicts_the_oldest() -> None:
    from ezchain_executor.pipeline import DedupeStore

    pipe, _, _ = pipeline()
    pipe.idempotency_store = DedupeStore(max_entries=2)

    for index in range(3):
        await pipe.run(buy(idempotency_key=f"bounded-{index}"), snapshot=make_snapshot())

    assert len(pipe.idempotency_store) == 2
    assert "bounded-0" not in pipe.idempotency_store
    assert "bounded-1" in pipe.idempotency_store
    assert "bounded-2" in pipe.idempotency_store


@pytest.mark.asyncio
async def test_the_kill_switch_is_rechecked_after_the_quote() -> None:
    # Arming during the venue round-trip must refuse the submit, not only the next intent.
    venue = ArmingVenue(state={"armed": False})
    pipe, _, journal = pipeline(venue, kill_switch=KillSwitch(read=lambda: venue.state["armed"]))
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "denied"
    assert result.reason == "KILL_SWITCH_ACTIVE"
    assert venue.quote_calls == 1
    assert venue.submit_calls == 0
    assert journal.groups == ()


@pytest.mark.asyncio
async def test_the_kill_switch_does_not_recall_an_exit_armed_after_the_position_rebound() -> None:
    # The signature-time re-read gates new entries only. A sell re-reads its position
    # between pricing and submit; an arm that lands during that await must not trap the
    # exit, which is bounded by the held quantity and cannot open a short.
    state = {"armed": False}
    reads = {"count": 0}

    async def position_source(intent: Intent) -> PositionState:
        reads["count"] += 1
        if reads["count"] == 2:  # the pre-submit re-bound, not the initial read
            state["armed"] = True
        return position("10", "20")

    pipe, venue, journal = pipeline(kill_switch=KillSwitch(read=lambda: state["armed"]))
    pipe.position_source = position_source
    result = await pipe.run(sell(), snapshot=make_snapshot())

    assert result.status == "filled"
    assert result.reason is None
    assert result.order is not None
    assert result.order.reduce_only is True
    assert venue.quote_calls == 1
    assert venue.submit_calls == 1
    assert journal.groups != ()


@pytest.mark.asyncio
async def test_an_armed_switch_settles_an_exit_and_refuses_an_entry_in_the_same_pass() -> None:
    # One armed switch, one pipeline: the sell settles because it only reduces risk,
    # while the buy is refused. An exit must never be blocked by an entry-only gate.
    pipe, venue, journal = pipeline(kill_switch=KillSwitch(read=lambda: True))

    exit_result = await pipe.run(sell(), snapshot=make_snapshot(), position=position("10", "20"))
    entry_result = await pipe.run(buy(idempotency_key="armed-entry"), snapshot=make_snapshot())

    assert exit_result.status == "filled"
    assert exit_result.order is not None
    assert exit_result.order.reduce_only is True
    assert entry_result.status == "denied"
    assert entry_result.reason == "KILL_SWITCH_ACTIVE"
    assert venue.submit_calls == 1
    assert len(journal.groups) == 1


@dataclass
class ArmingPrepareVenue(FakeVenue):
    """Changes the switch while the unsigned transaction is being built.

    This is the window between the quote and the signature: the intent has been priced
    and risk-approved, the builder is awaiting, and the operator arms. Every read and
    every stage call is appended to ``events`` in order, so a test can prove *where* the
    read that refused the order sat.
    """

    arms_to: str = "armed"
    state: dict[str, str] = field(default_factory=lambda: {"mode": "released"})
    events: list[str] = field(default_factory=list)

    async def quote(self, intent):  # type: ignore[no-untyped-def]
        self.events.append("quote")
        return await super().quote(intent)

    async def prepare(self, approved):  # type: ignore[no-untyped-def]
        raw = await super().prepare(approved)
        self.events.append("prepare")
        self.state["mode"] = self.arms_to
        return raw

    async def submit(self, order, signed=None):  # type: ignore[no-untyped-def]
        self.events.append("submit")
        return await super().submit(order, signed)


@dataclass
class RecordingKeyRing:
    """A keyring that records when a signature was produced."""

    events: list[str]
    calls: int = 0

    def sign(self, payload: bytes) -> bytes:
        self.events.append("sign")
        self.calls += 1
        return b"test-signature"


def _switch_read(venue: ArmingPrepareVenue):  # type: ignore[no-untyped-def]
    """A reader over the venue's mode: armed, released, or unreadable."""

    def read() -> bool:
        venue.events.append("read")
        mode = venue.state["mode"]
        if mode == "unreadable":
            raise RuntimeError("system_flags unreadable")
        return mode == "armed"

    return read


def _signing_pipeline(venue: ArmingPrepareVenue):  # type: ignore[no-untyped-def]
    keyring = RecordingKeyRing(venue.events)
    pipe, _, journal = pipeline(venue, kill_switch=KillSwitch(read=_switch_read(venue)))
    pipe.signing = SigningBoundary(
        cluster="devnet",
        recipient="Wallet11111111111111111111111111111111111111",
        program_id="JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
        adapter=venue,
        signer=LocalTransactionSigner(keyring),
    )
    return pipe, keyring, journal


@pytest.mark.asyncio
async def test_one_buying_intent_crosses_the_switch_four_times_inside_one_intent() -> None:
    # "Within one scheduling tick, measured": the count is the measurement. Every stage
    # that waits — the quote, the builder, the signer, the venue — is bracketed by a fresh
    # read, so the value that decides is never older than the stage it gates. This test
    # measures one intent, not one tick; the tick property is
    # tests/engine/test_scheduler.py::test_an_arm_inside_a_tick_is_obeyed_by_that_same_tick.
    venue = ArmingPrepareVenue(arms_to="released")
    pipe, keyring, journal = _signing_pipeline(venue)

    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "filled"
    assert keyring.calls == 1
    assert venue.events == ["read", "quote", "read", "prepare", "read", "sign", "read", "submit"]
    assert venue.events.count("read") == 4
    assert journal.groups != ()


@pytest.mark.asyncio
async def test_an_arm_between_quote_and_sign_refuses_before_the_key_is_touched() -> None:
    # The arm lands after the quote and before the signature. A read taken only after
    # signing cannot help: the key would already have been spent. The refusing read must
    # therefore sit after the builder round-trip and before the signer call.
    venue = ArmingPrepareVenue()
    pipe, keyring, journal = _signing_pipeline(venue)

    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert keyring.calls == 0  # no signature exists
    assert "sign" not in venue.events
    assert venue.events[-1] == "read"  # the read after the build is the one that refused
    assert venue.events[venue.events.index("prepare") + 1] == "read"
    assert result.status == "denied"
    assert result.reason == "KILL_SWITCH_ACTIVE"
    assert result.order is None
    assert venue.submit_calls == 0
    assert journal.groups == ()


@pytest.mark.asyncio
async def test_an_unreadable_switch_between_quote_and_sign_refuses_before_the_key() -> None:
    # An unreadable switch at sign time is a refusal, never "off".
    venue = ArmingPrepareVenue(arms_to="unreadable")
    pipe, keyring, journal = _signing_pipeline(venue)

    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert venue.prepare_calls == 1
    assert keyring.calls == 0
    assert result.status == "denied"
    assert result.reason == "KILL_SWITCH_UNAVAILABLE"
    assert venue.submit_calls == 0
    assert journal.groups == ()


@pytest.mark.asyncio
async def test_an_exit_armed_between_quote_and_sign_still_signs_and_settles() -> None:
    # The sign-time gate is entry-only, like every other kill-switch read: an exit is
    # bounded by the held quantity and may not be recalled, or an arm would trap funds.
    venue = ArmingPrepareVenue()
    pipe, _, journal = pipeline(
        venue, kill_switch=KillSwitch(read=lambda: venue.state["mode"] == "armed")
    )
    pipe.signing = SigningBoundary(
        cluster="devnet",
        recipient="Wallet11111111111111111111111111111111111111",
        program_id="JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
        adapter=venue,
        signer=LocalTransactionSigner(_RecordingKeyRing()),
    )

    result = await pipe.run(sell(), snapshot=make_snapshot(), position=position("10", "20"))

    assert result.status == "filled"
    assert result.reason is None
    assert venue.submit_calls == 1
    assert journal.groups != ()


@dataclass
class _RecordingKeyRing:
    """A keyring that only counts; it never arms anything."""

    calls: int = 0

    def sign(self, payload: bytes) -> bytes:
        self.calls += 1
        return b"test-signature"


@pytest.mark.asyncio
async def test_a_buy_armed_during_signing_is_refused_before_the_venue_submit() -> None:
    # Signing awaits too: an arm that lands there must refuse the submit, not the next
    # intent. The signed transaction is discarded and the venue never sees it.
    keyring = ArmingKeyRing()
    pipe, venue, journal = pipeline(kill_switch=KillSwitch(read=lambda: keyring.state["armed"]))
    pipe.signing = SigningBoundary(
        cluster="devnet",
        recipient="Wallet11111111111111111111111111111111111111",
        program_id="JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
        adapter=venue,
        signer=LocalTransactionSigner(keyring),
    )

    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "denied"
    assert result.reason == "KILL_SWITCH_ACTIVE"
    assert keyring.calls == 1  # the signature was produced, then discarded
    assert venue.submit_calls == 0
    assert journal.groups == ()


@pytest.mark.asyncio
async def test_an_armed_switch_is_never_served_from_an_earlier_released_read() -> None:
    # The reader is the only source of truth: the released value observed at the start of
    # a run cannot survive the arm that lands before the signature.
    venue = ArmingVenue(state={"armed": False})
    reads = {"count": 0}

    def read() -> bool:
        reads["count"] += 1
        return bool(venue.state["armed"])

    pipe, _, _ = pipeline(venue, kill_switch=KillSwitch(read=read))
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.reason == "KILL_SWITCH_ACTIVE"
    assert venue.submit_calls == 0
    # The pre-quote read saw released; the next read, after the quote armed the switch,
    # crossed the process boundary again instead of reusing that value.
    assert reads["count"] == 2


def _sum_usd(entries: tuple[LedgerEntry, ...]) -> Decimal:
    return sum((entry.usd_value for entry in entries), Decimal(0))
