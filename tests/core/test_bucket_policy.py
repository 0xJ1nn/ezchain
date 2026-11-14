"""The bucket arithmetic: a cap on net cash deployed, and the reduce-only exception.

This module is pure policy, so these tests need no database. The properties pinned here
are the ones the order path relies on: the boundary itself is spendable, a fee counts, an
exit is never refused, and an uncapped bucket produces no refusal at all.
"""

from __future__ import annotations

from decimal import Decimal

from ezchain_core.bucket import BucketState, bucket_state, check_bucket


def _state(cap: str | None, cash: str) -> BucketState:
    return bucket_state(
        "key-1",
        cap_usd=None if cap is None else Decimal(cap),
        bucket_cash_usd=Decimal(cash),
    )


def test_deployment_is_the_negation_of_attributed_cash() -> None:
    # The agent spent 100 from the account; it has 100 deployed.
    assert _state("250", "-100").deployed_usd == Decimal("100")
    # An exit returned 20 more than was deployed, so the bucket is in credit.
    assert _state("250", "20").deployed_usd == Decimal("-20")


def test_available_is_the_cap_minus_the_deployment() -> None:
    assert _state("250", "-100").available_usd == Decimal("150")
    assert _state("250", "20").available_usd == Decimal("270")


def test_spending_exactly_to_the_cap_is_allowed() -> None:
    assert check_bucket(_state("100", "0"), Decimal("100")) is None


def test_a_cent_past_the_cap_is_refused() -> None:
    refusal = check_bucket(_state("100", "0"), Decimal("100.01"))

    assert refusal is not None
    assert refusal.code == "AGENT_BUCKET_EXCEEDED"
    assert refusal.detail["available_usd"] == "100"


def test_the_fee_is_part_of_the_worst_case() -> None:
    """Notional alone may fit while notional plus fee does not."""
    state = _state("100", "0")

    assert check_bucket(state, Decimal("99.5")) is None
    refused = check_bucket(state, Decimal("99.5"), fee_usd=Decimal("0.6"))

    assert refused is not None
    assert refused.detail["worst_case_usd"] == "100.1"
    assert refused.detail["fee_usd"] == "0.6"


def test_an_uncapped_bucket_never_refuses() -> None:
    state = _state(None, "0")

    assert state.capped is False
    assert state.available_usd is None
    assert check_bucket(state, Decimal("1000000")) is None


def test_a_cap_at_zero_refuses_any_entry() -> None:
    refusal = check_bucket(_state("0", "0"), Decimal("0.00000001"))

    assert refusal is not None
    assert refusal.code == "AGENT_BUCKET_EXCEEDED"


def test_credit_from_an_exit_extends_what_may_be_deployed() -> None:
    """A profitable exit returns more than it deployed, so the next entry has more room."""
    in_credit = _state("100", "25")

    assert in_credit.available_usd == Decimal("125")
    assert check_bucket(in_credit, Decimal("125")) is None
    assert check_bucket(in_credit, Decimal("125.01")) is not None


def test_the_refusal_carries_every_number_a_person_needs() -> None:
    refusal = check_bucket(_state("250", "-200"), Decimal("75"), fee_usd=Decimal("1"))

    assert refusal is not None
    assert refusal.detail == {
        "agent_id": "key-1",
        "cap_usd": "250",
        "deployed_usd": "200",
        "available_usd": "50",
        "requested_usd": "75",
        "fee_usd": "1",
        "worst_case_usd": "76",
    }


def test_the_state_reports_itself_as_dollars() -> None:
    assert _state("250", "-100").to_dict() == {
        "agent_id": "key-1",
        "cap_usd": "250",
        "deployed_usd": "100",
        "available_usd": "150",
        "capped": True,
        "mode": "paper",
    }
