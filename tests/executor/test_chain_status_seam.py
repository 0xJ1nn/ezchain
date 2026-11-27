"""The executor outbox can ask a chain status reader, but a hash is never a Fill.

The reconciliation seam is deliberately narrower than a venue: a chain confirmation proves
a transaction settled, not the executed amount, price or fee that the ledger would need.
So ``confirmed`` leaves the claim pending, ``failed`` resolves it as never filled, and an
unreadable status leaves it pending. No journal entry is written from chain status alone.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from ezchain_core import utcnow
from ezchain_executor import (
    BroadcastAttempt,
    ChainConfirmationVenue,
    MemoryAttemptLog,
    MemoryJournal,
    Reconciler,
)


class _Reader:
    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        self.seen: list[str] = []

    async def status(self, tx_hash: str) -> object:
        self.seen.append(tx_hash)
        return SimpleNamespace(outcome=self.outcome, chain="solana", tx_hash=tx_hash)


def claim() -> BroadcastAttempt:
    from decimal import Decimal

    return BroadcastAttempt(
        id="attempt-chain-status",
        group_id="fill:order-chain-status",
        chain="solana",
        token_address="Mint1111111111111111111111111111111111111111",
        side="buy",
        avg_cost_usd=Decimal("2"),
        position_cost_usd=Decimal("0"),
        transaction_digest="public-tx-hash",
        created_at=utcnow() - timedelta(minutes=10),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("confirmed", "unknown"),
        ("failed", "not_found"),
        ("pending", "unknown"),
        ("unknown", "unknown"),
    ],
)
async def test_chain_outcomes_map_without_inventing_a_fill(outcome: str, expected: str) -> None:
    reader = _Reader(outcome)
    venue = ChainConfirmationVenue(reader)
    status = await venue.transaction_status(
        order_id="order-chain-status", transaction_digest="public-tx-hash"
    )
    assert status.state == expected
    assert status.fill is None
    assert reader.seen == ["public-tx-hash"]


@pytest.mark.asyncio
async def test_confirmed_chain_status_never_journals_a_claim() -> None:
    log = MemoryAttemptLog()
    log.append(claim())
    journal = MemoryJournal()
    venue = ChainConfirmationVenue(_Reader("confirmed"))
    results = await Reconciler(attempts=log, sink=journal, venue=venue, min_age_seconds=1).run()
    assert len(results) == 1
    assert results[0].outcome == "pending"
    assert journal.entries == ()


@pytest.mark.asyncio
async def test_failed_chain_status_resolves_a_claim_as_never_filled() -> None:
    log = MemoryAttemptLog()
    log.append(claim())
    journal = MemoryJournal()
    venue = ChainConfirmationVenue(_Reader("failed"))
    results = await Reconciler(attempts=log, sink=journal, venue=venue, min_age_seconds=1).run()
    assert results[0].outcome == "failed"
    assert journal.entries == ()


@pytest.mark.asyncio
async def test_missing_digest_is_not_asked_about_and_stays_pending() -> None:
    reader = _Reader("confirmed")
    venue = ChainConfirmationVenue(reader)
    status = await venue.transaction_status(order_id="order", transaction_digest=None)
    assert status.state == "unknown"
    assert reader.seen == []


@pytest.mark.asyncio
async def test_unreadable_status_reader_leaves_the_claim_pending() -> None:
    class Broken:
        async def status(self, tx_hash: str) -> object:
            raise RuntimeError("rpc down")

    venue = ChainConfirmationVenue(Broken())
    status = await venue.transaction_status(order_id="order", transaction_digest="public-tx-hash")
    assert status.state == "unknown"
    assert status.fill is None
