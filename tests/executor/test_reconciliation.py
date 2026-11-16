"""A crash between broadcast and journal is found and booked exactly once.

The outbox is written before the venue sees the order, so a process that dies before the
ledger append leaves a claim. A later pass asks the venue what actually happened and
books the receipt from the claim's persisted inputs. The tests here drive a real
file-backed outbox across a simulated restart, a venue that never filled, and a venue
whose answer is unavailable, and they check that a reconcile beside a live fill is inert.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from ezchain_core import (
    Fill,
    Intent,
    Journal,
    PairInfo,
    PositionState,
    RiskLimits,
    SourceRef,
    TokenSnapshot,
    utcnow,
)
from ezchain_executor import (
    AttemptLog,
    BroadcastAttempt,
    ExecutionPipeline,
    FakeVenue,
    FileAttemptLog,
    MemoryAttemptLog,
    MemoryJournal,
    Reconciler,
    VenueTxStatus,
)

CHAIN = "solana"
TOKEN = "Mint1111111111111111111111111111111111111111"


class _BrokenSink:
    """The venue filled; the ledger append never completed."""

    def append(self, journal: Journal) -> None:
        raise RuntimeError("process died before the journal")


class _BlindAttemptLog:
    """An outbox that refuses the claim: no broadcast may happen."""

    def append(self, attempt: BroadcastAttempt) -> None:
        raise RuntimeError("the outbox is read-only")

    def pending(self, *, before: Any) -> tuple[BroadcastAttempt, ...]:
        return ()

    def resolve(self, attempt_id: str, *, state: str, reason: str | None = None) -> None:
        return None


class _ForgetfulAttemptLog(MemoryAttemptLog):
    """A pass that books but loses its resolution must not book twice."""

    def resolve(self, attempt_id: str, *, state: str, reason: str | None = None) -> None:
        return None


class _ObservedVenue(FakeVenue):
    """Checks the claim is durable at the moment submit is called."""

    def __init__(self, log: AttemptLog, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._log = log
        self.claim_at_submit = -1

    async def submit(self, order: Any, signed: Any = None) -> Fill:
        self.claim_at_submit = len(self._log.pending(before=utcnow() + timedelta(seconds=1)))
        return await super().submit(order, signed)


class _VagueVenue(FakeVenue):
    """A venue whose status read is unavailable, then recovers."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.status_mode = "unknown"

    async def transaction_status(
        self, *, order_id: str, transaction_digest: str | None
    ) -> VenueTxStatus:
        if self.status_mode == "unknown":
            return VenueTxStatus("unknown")
        return await super().transaction_status(
            order_id=order_id, transaction_digest=transaction_digest
        )


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


def snapshot(price: str = "2") -> TokenSnapshot:
    observed = utcnow()
    return TokenSnapshot(
        chain=CHAIN,
        address=TOKEN,
        symbol="TEST",
        name="Test",
        price_usd=Decimal(price),
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
                price_usd=Decimal(price),
                liquidity_usd=Decimal("500000"),
                volume_24h_usd=Decimal("1000000"),
                url=None,
            ),
        ),
        source=SourceRef("dexscreener", None, observed),
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


def make_pipe(venue: FakeVenue, sink: Any, log: AttemptLog) -> ExecutionPipeline:
    return ExecutionPipeline(
        venue=venue,
        sink=sink,
        limits=limits(),
        timeout_seconds=0.2,
        attempts=log,
    )


@pytest.mark.asyncio
async def test_a_crash_after_broadcast_leaves_a_claim_a_new_process_can_book(
    tmp_path: Path,
) -> None:
    """A file-backed claim survives the process that wrote it, and the fill is booked once."""
    path = tmp_path / "attempts.jsonl"
    log = FileAttemptLog(path)
    venue = FakeVenue(fee_usd=Decimal("1"))
    result = await make_pipe(venue, _BrokenSink(), log).run(buy(), snapshot=snapshot())

    assert result.status == "journal_error"
    assert result.fill is not None
    assert len(log.pending(before=utcnow())) == 1

    # A new object, reading the file left on disk, stands in for a restarted process.
    recovered = FileAttemptLog(path)
    assert len(recovered.pending(before=utcnow())) == 1

    ledger = MemoryJournal()
    outcomes = await Reconciler(
        attempts=recovered, sink=ledger, venue=venue, min_age_seconds=0
    ).run()

    assert [item.outcome for item in outcomes] == ["journaled"]
    assert ledger.groups == (f"fill:{result.fill.order_id}",)
    assert sum(entry.usd_value for entry in ledger.entries) == Decimal("0")
    assert recovered.pending(before=utcnow()) == ()


@pytest.mark.asyncio
async def test_the_claim_is_written_before_the_venue_sees_the_order(tmp_path: Path) -> None:
    log = FileAttemptLog(tmp_path / "attempts.jsonl")
    venue = _ObservedVenue(log)

    result = await make_pipe(venue, MemoryJournal(), log).run(buy(), snapshot=snapshot())

    assert result.status == "filled"
    assert venue.claim_at_submit == 1, "submit saw no durable claim for its own order"


@pytest.mark.asyncio
async def test_an_outbox_that_refuses_the_claim_prevents_the_broadcast() -> None:
    venue = FakeVenue()
    result = await make_pipe(venue, MemoryJournal(), _BlindAttemptLog()).run(
        buy(), snapshot=snapshot()
    )

    assert result.status == "rejected"
    assert result.reason == "RECONCILIATION_FAILED"
    assert venue.submit_calls == 0
    assert result.fill is None


@pytest.mark.asyncio
async def test_a_reconcile_while_a_claim_is_still_live_touches_nothing() -> None:
    """A live submit's claim is younger than the grace window, so it is left alone."""
    log = MemoryAttemptLog()
    venue = FakeVenue()
    ledger = MemoryJournal()
    await make_pipe(venue, ledger, log).run(buy(), snapshot=snapshot())
    # The ordinary run resolves its claim; a fresh one stands for a fill in flight.
    fresh = BroadcastAttempt(
        id="order:in-flight",
        group_id="fill:order:in-flight",
        chain=CHAIN,
        token_address=TOKEN,
        side="buy",
        avg_cost_usd=Decimal("0"),
        position_cost_usd=Decimal("0"),
        transaction_digest=None,
        created_at=utcnow(),
    )
    log.append(fresh)

    outcomes = await Reconciler(attempts=log, sink=ledger, venue=venue).run()

    assert outcomes == ()
    assert len(ledger) == 1


@pytest.mark.asyncio
async def test_a_venue_that_never_filled_fails_the_claim_with_a_stable_code() -> None:
    log = MemoryAttemptLog()
    venue = FakeVenue()
    await make_pipe(venue, _BrokenSink(), log).run(buy(), snapshot=snapshot())
    venue.status_mode = "not_found"
    ledger = MemoryJournal()

    outcomes = await Reconciler(attempts=log, sink=ledger, venue=venue, min_age_seconds=0).run()

    assert [(item.outcome, item.reason) for item in outcomes] == [("failed", "VENUE_REJECTED")]
    assert log.pending(before=utcnow()) == ()
    assert len(ledger) == 0


@pytest.mark.asyncio
async def test_an_unavailable_status_keeps_the_claim_open_until_it_can_be_answered() -> None:
    log = MemoryAttemptLog()
    venue = _VagueVenue()
    await make_pipe(venue, _BrokenSink(), log).run(buy(), snapshot=snapshot())
    ledger = MemoryJournal()

    first = await Reconciler(attempts=log, sink=ledger, venue=venue, min_age_seconds=0).run()
    assert [(item.outcome, item.reason) for item in first] == [("pending", "RECONCILIATION_FAILED")]
    assert len(log.pending(before=utcnow())) == 1
    assert len(ledger) == 0

    venue.status_mode = "normal"
    second = await Reconciler(attempts=log, sink=ledger, venue=venue, min_age_seconds=0).run()
    assert [item.outcome for item in second] == ["journaled"]
    assert len(ledger) == 1
    assert log.pending(before=utcnow()) == ()


@pytest.mark.asyncio
async def test_a_lost_resolution_does_not_book_the_same_fill_twice() -> None:
    """A pass that cannot record its resolution must not duplicate the journal."""
    log = _ForgetfulAttemptLog()
    venue = FakeVenue()
    await make_pipe(venue, _BrokenSink(), log).run(buy(), snapshot=snapshot())
    ledger = MemoryJournal()
    reconciler = Reconciler(attempts=log, sink=ledger, venue=venue, min_age_seconds=0)

    assert [item.outcome for item in await reconciler.run()] == ["journaled"]
    assert [item.outcome for item in await reconciler.run()] == ["journaled"]
    assert len(ledger) == 1


@pytest.mark.asyncio
async def test_a_partial_sell_is_reconciled_from_the_receipt_not_the_order() -> None:
    """The late journal uses the venue's fill quantity and its derived cost basis."""
    log = MemoryAttemptLog()
    venue = FakeVenue(mode="partial", partial_ratio=Decimal("0.5"), fee_usd=Decimal("1"))
    held = position(quantity="20", cost="40")
    pipe = make_pipe(venue, _BrokenSink(), log)
    result = await pipe.run(sell(), snapshot=snapshot(), position=held)
    assert result.status == "journal_error"
    assert result.fill is not None
    assert result.fill.quantity == Decimal("5")

    ledger = MemoryJournal()
    outcomes = await Reconciler(attempts=log, sink=ledger, venue=venue, min_age_seconds=0).run()

    assert [item.outcome for item in outcomes] == ["journaled"]
    booked = ledger.entries
    position_leg = next(entry for entry in booked if entry.account.startswith("position:"))
    assert position_leg.amount == -result.fill.quantity
    assert next(entry for entry in booked if entry.account == "cash:USD").usd_value == Decimal(
        "10"
    )  # 5 tokens at $2, from the receipt
    assert sum(entry.usd_value for entry in booked) == Decimal("0")
