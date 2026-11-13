"""Idempotency, cash checks and fail-closed risk integration."""

from __future__ import annotations

from dataclasses import fields
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from ezchain_api.models import LedgerEntryRow, PaperFillRow, PaperOrderRow
from ezchain_api.services import paper
from ezchain_api.services.paper import IdempotencyConflict
from ezchain_core import OrderRequest, utcnow

from .conftest import CHAIN, TOKEN, make_request, make_settings, make_snapshot, place


def test_idempotent_retry_returns_the_original_order(session, settings, dex, jupiter) -> None:
    request = make_request(amount_usd="100", key="retry-key-1")
    first = place(session, settings, dex, jupiter, request)
    second = place(session, settings, dex, jupiter, request)

    assert first.status == "filled"
    assert second.id == first.id
    assert session.scalar(select(func.count()).select_from(PaperOrderRow)) == 1
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 1
    # genesis + one balanced order group
    assert session.scalar(select(func.count()).select_from(LedgerEntryRow)) == 6


def test_rejected_order_is_also_idempotent(session, settings, dex, jupiter) -> None:
    request = make_request(side="sell", amount_usd="100", key="retry-reject")
    first = place(session, settings, dex, jupiter, request)
    second = place(session, settings, dex, jupiter, request)
    assert first.status == "rejected"
    assert second.id == first.id
    assert session.scalar(select(func.count()).select_from(PaperOrderRow)) == 1


@pytest.mark.parametrize(
    "changed",
    [
        pytest.param({"amount_usd": "900"}, id="amount"),
        pytest.param({"side": "sell"}, id="side"),
        pytest.param({"token": "0x0000000000000000000000000000000000000def"}, id="token"),
        pytest.param({"chain": "solana"}, id="chain"),
        pytest.param({"strategy_id": "strategy-2"}, id="strategy"),
        pytest.param({"reduce_only": True}, id="reduce-only"),
        pytest.param({"quantity": "1"}, id="quantity"),
    ],
)
def test_idempotency_conflicts_on_payload_mismatch(
    session, settings, dex, jupiter, changed
) -> None:
    first = place(session, settings, dex, jupiter, make_request(amount_usd="50", key="same"))

    with pytest.raises(IdempotencyConflict):
        place(
            session,
            settings,
            dex,
            jupiter,
            make_request(**{"amount_usd": "50", "key": "same", **changed}),
        )

    # The original order is left untouched, and no second fill or order exists.
    assert first.amount_usd == Decimal("50")
    assert session.scalar(select(func.count()).select_from(PaperOrderRow)) == 1
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 1


def test_fingerprint_is_canonical_across_order_scale_and_absent_optionals() -> None:
    """Field order, decimal scale and null-vs-missing must not change the hash."""
    first = paper._fingerprint(
        {"chain": CHAIN, "amount_usd": Decimal("50.00"), "strategy_id": None}
    )
    second = paper._fingerprint({"amount_usd": Decimal("50"), "chain": CHAIN})
    third = paper._fingerprint({"chain": CHAIN, "amount_usd": Decimal("5E+1")})
    assert first == second == third


def test_fingerprint_covers_every_order_request_field() -> None:
    covered = set(paper._FINGERPRINT_FIELDS) | {"idempotency_key"}
    assert covered == {field.name for field in fields(OrderRequest)}


def test_replay_accepts_a_differently_spelled_amount(session, settings, dex, jupiter) -> None:
    first = place(session, settings, dex, jupiter, make_request(amount_usd="50", key="scale"))
    replay = place(session, settings, dex, jupiter, make_request(amount_usd="50.00", key="scale"))

    assert replay.id == first.id
    assert session.scalar(select(func.count()).select_from(PaperOrderRow)) == 1
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 1


def test_legacy_order_without_a_fingerprint_replays_only_the_same_payload(
    session, settings, dex, jupiter
) -> None:
    # A row written before migration 0011 has no stored hash. The test stands in for
    # an already-filled order: the replay path reads the order row, never its fills.
    session.add(
        PaperOrderRow(
            id="legacy-order",
            idempotency_key="legacy-key",
            request_fingerprint=None,
            chain=CHAIN,
            token_address=TOKEN,
            side="buy",
            amount_usd=Decimal("100"),
            status="filled",
            created_at=utcnow(),
        )
    )
    session.commit()

    replay = place(
        session, settings, dex, jupiter, make_request(amount_usd="100.00", key="legacy-key")
    )
    assert replay.id == "legacy-order"
    # The derived hash is persisted so the row is checked directly next time.
    assert replay.request_fingerprint is not None
    assert replay.request_fingerprint == paper._request_fingerprint(
        make_request(amount_usd="100", key="legacy-key")
    )

    with pytest.raises(IdempotencyConflict):
        place(session, settings, dex, jupiter, make_request(amount_usd="900", key="legacy-key"))
    assert session.scalar(select(func.count()).select_from(PaperOrderRow)) == 1


def test_a_stale_mark_refuses_to_price_a_buy_before_a_fill(session, settings, dex, jupiter) -> None:
    """A mark past its budget is not current, so no quote is built from it at all.

    The refusal is a persisted rejection row with the router's reason code, exactly as a
    kill-switch refusal is, so the operator can read why no fill happened. QUOTE_STALE
    covers an old *venue* quote and is exercised end-to-end in ``test_reduce_only``.
    """
    dex.set(make_snapshot(price=Decimal("2000"), observed_at=utcnow() - timedelta(seconds=120)))

    order = place(session, settings, dex, jupiter, make_request(amount_usd="100", key="stale-mark"))
    assert order.status == "rejected"
    assert order.reason == "SNAPSHOT_STALE"
    assert order.quote_json is not None
    assert order.quote_json["error"]["code"] == "SNAPSHOT_STALE"
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 0
    assert session.scalar(select(func.count()).select_from(PaperOrderRow)) == 1


def test_unknown_liquidity_is_rejected(session, settings, dex, jupiter) -> None:
    dex.set(make_snapshot(price=Decimal("2000"), liquidity=None))
    order = place(session, settings, dex, jupiter, make_request(amount_usd="100", key="liq-none"))
    assert order.status == "rejected"
    assert order.reason == "LIQUIDITY_UNKNOWN"


def test_unknown_pair_age_rejects_a_buy(session, settings, dex, jupiter) -> None:
    dex.set(make_snapshot(pair_created_at=None))
    order = place(session, settings, dex, jupiter, make_request(amount_usd="100", key="age-none"))
    assert order.status == "rejected"
    assert order.reason == "PAIR_AGE_UNKNOWN"


def test_young_pair_rejects_a_buy(session, settings, dex, jupiter) -> None:
    dex.set(make_snapshot(pair_created_at=utcnow() - timedelta(minutes=5)))
    order = place(session, settings, dex, jupiter, make_request(amount_usd="100", key="age-young"))
    assert order.status == "rejected"
    assert order.reason == "PAIR_TOO_YOUNG"


def test_low_liquidity_is_rejected(session, settings, dex, jupiter) -> None:
    dex.set(make_snapshot(price=Decimal("2000"), liquidity=Decimal("5000")))
    order = place(session, settings, dex, jupiter, make_request(amount_usd="100", key="liq-low"))
    assert order.status == "rejected"
    assert order.reason == "LIQUIDITY_TOO_LOW"


def test_paper_mark_size_cap_is_enforced(session, settings, dex, jupiter) -> None:
    dex.set(make_snapshot(price=Decimal("2000")))
    order = place(session, settings, dex, jupiter, make_request(amount_usd="300", key="too-big"))
    assert order.status == "rejected"
    assert order.reason == "PAPER_MARK_LIMIT"


def test_order_limit_is_enforced(session, settings, dex, jupiter) -> None:
    dex.set(make_snapshot(price=Decimal("2000")))
    order = place(session, settings, dex, jupiter, make_request(amount_usd="1500", key="order-cap"))
    assert order.status == "rejected"
    assert order.reason is not None and "ORDER_LIMIT" in order.reason


def test_insufficient_cash_is_rejected_without_going_negative(session, dex, jupiter) -> None:
    poor = make_settings(paper_starting_cash_usd=Decimal("50"))
    dex.set(make_snapshot(price=Decimal("1"), liquidity=Decimal("1000000")))

    order = place(session, poor, dex, jupiter, make_request(amount_usd="50", key="cash-1"))
    assert order.status == "rejected"
    assert order.reason == "INSUFFICIENT_CASH"
    account = paper.account_snapshot(session)
    assert Decimal(account["cash_usd"]) == Decimal("50")
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 0
