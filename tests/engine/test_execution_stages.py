"""Unit tests for the typed execution stages in ``ezchain_core.execution``.

These are the I/O-free objects the pipeline drives. They must be constructible only with
a real size, must not fabricate a zero, and must book a journal from the receipt.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from ezchain_core import Fill, Intent, PositionState, RiskDecision, journal_from_fill
from ezchain_core.execution import risk_decision_from_intent
from ezchain_core.types import RiskReason

CHAIN = "solana"
TOKEN = "Mint1111111111111111111111111111111111111111"


def test_an_intent_requires_a_size_and_a_key() -> None:
    with pytest.raises(ValueError):
        Intent(
            side="buy",
            amount_usd=Decimal("0"),
            chain=CHAIN,
            token_address=TOKEN,
            idempotency_key="k",
        )
    with pytest.raises(ValueError):
        Intent(
            side="buy",
            amount_usd=Decimal("100"),
            chain=CHAIN,
            token_address=TOKEN,
            idempotency_key="",
        )


def test_a_sell_may_be_sized_by_quantity_with_a_zero_usd_hint() -> None:
    intent = Intent(
        side="sell",
        amount_usd=Decimal("0"),
        chain=CHAIN,
        token_address=TOKEN,
        idempotency_key="exit",
        quantity=Decimal("5"),
    )
    assert intent.quantity == Decimal("5")


def test_a_buy_must_be_sized_in_usd() -> None:
    with pytest.raises(ValueError):
        Intent(
            side="buy",
            amount_usd=Decimal("0"),
            chain=CHAIN,
            token_address=TOKEN,
            idempotency_key="k",
            quantity=Decimal("5"),
        )


def test_sized_returns_a_new_intent_with_the_resolved_size() -> None:
    intent = Intent(
        side="sell",
        amount_usd=Decimal("0"),
        chain=CHAIN,
        token_address=TOKEN,
        idempotency_key="exit",
        reduce_only=True,
        quantity=Decimal("50"),
    )
    sized = intent.sized(amount_usd=Decimal("100"), quantity=Decimal("25"))
    assert sized.quantity == Decimal("25")
    assert sized.amount_usd == Decimal("100")
    assert sized.reduce_only is True
    assert intent.quantity == Decimal("50")


def test_a_fill_is_bookable_only_when_accepted_and_sized() -> None:
    good = Fill(
        order_id="o",
        side="buy",
        quantity=Decimal("1"),
        price_usd=Decimal("2"),
        notional_usd=Decimal("2"),
        fee_usd=Decimal("0"),
    )
    assert good.bookable
    rejected = Fill(
        order_id="o",
        side="buy",
        quantity=Decimal("1"),
        price_usd=Decimal("2"),
        notional_usd=Decimal("2"),
        fee_usd=Decimal("0"),
        status="rejected",
    )
    assert not rejected.bookable
    empty = Fill(
        order_id="o",
        side="buy",
        quantity=Decimal("0"),
        price_usd=Decimal("2"),
        notional_usd=Decimal("0"),
        fee_usd=Decimal("0"),
    )
    assert not empty.bookable


def test_a_journal_cannot_be_built_from_an_unbookable_fill() -> None:
    fill = Fill(
        order_id="o",
        side="buy",
        quantity=Decimal("0"),
        price_usd=Decimal("2"),
        notional_usd=Decimal("0"),
        fee_usd=Decimal("0"),
    )
    with pytest.raises(ValueError):
        journal_from_fill(fill, chain=CHAIN, token_address=TOKEN)


def test_a_buy_journal_is_balanced_and_books_the_notional() -> None:
    fill = Fill(
        order_id="o",
        side="buy",
        quantity=Decimal("2"),
        price_usd=Decimal("50"),
        notional_usd=Decimal("100"),
        fee_usd=Decimal("1"),
    )
    journal = journal_from_fill(fill, chain=CHAIN, token_address=TOKEN)
    assert journal.group_id == "fill:o"
    assert sum(entry.usd_value for entry in journal.entries) == Decimal("0")
    position = next(e for e in journal.entries if e.account.startswith("position:"))
    assert position.amount == Decimal("2")


def test_a_sell_journal_removes_the_receipt_quantity() -> None:
    fill = Fill(
        order_id="o",
        side="sell",
        quantity=Decimal("3"),
        price_usd=Decimal("40"),
        notional_usd=Decimal("120"),
        fee_usd=Decimal("0"),
    )
    journal = journal_from_fill(fill, chain=CHAIN, token_address=TOKEN, avg_cost_usd=Decimal("30"))
    assert sum(entry.usd_value for entry in journal.entries) == Decimal("0")
    position = next(e for e in journal.entries if e.account.startswith("position:"))
    assert position.amount == Decimal("-3")


def test_a_missing_mark_denies_a_buy_but_not_a_sell() -> None:
    buy = Intent(
        side="buy", amount_usd=Decimal("10"), chain=CHAIN, token_address=TOKEN, idempotency_key="b"
    )
    denied = risk_decision_from_intent(intent=buy, snapshot=None)
    assert denied.allowed is False
    assert RiskReason("MISSING_RISK_INPUTS") in denied.reasons

    sell = Intent(
        side="sell",
        amount_usd=Decimal("0"),
        chain=CHAIN,
        token_address=TOKEN,
        idempotency_key="s",
        quantity=Decimal("5"),
    )
    allowed = risk_decision_from_intent(intent=sell, snapshot=None)
    assert allowed.allowed is True


def test_a_reduce_only_exit_does_not_report_an_entry_cap() -> None:
    decision = risk_decision_from_intent(
        intent=Intent(
            side="sell",
            amount_usd=Decimal("0"),
            chain=CHAIN,
            token_address=TOKEN,
            idempotency_key="s",
            quantity=Decimal("5"),
            reduce_only=True,
        ),
        snapshot=None,
        position=PositionState(CHAIN, TOKEN, "T", Decimal("5"), Decimal("10")),
    )
    assert decision.max_order_usd is None
    assert isinstance(decision, RiskDecision)
