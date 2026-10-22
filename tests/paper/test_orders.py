"""Idempotency, cash checks and fail-closed risk integration."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select

from ezchain_api.models import LedgerEntryRow, PaperFillRow, PaperOrderRow
from ezchain_api.services import paper
from ezchain_core import utcnow

from .conftest import make_request, make_settings, make_snapshot, place


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


def test_idempotency_is_unique_per_key_not_per_payload(session, settings, dex, jupiter) -> None:
    first = place(session, settings, dex, jupiter, make_request(amount_usd="50", key="same"))
    second = place(session, settings, dex, jupiter, make_request(amount_usd="900", key="same"))
    assert second.id == first.id
    assert second.amount_usd == Decimal("50")
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 1


def test_stale_quote_is_rejected_before_a_fill(session, settings, dex, jupiter) -> None:
    dex.set(make_snapshot(price=Decimal("2000"), observed_at=utcnow() - timedelta(seconds=120)))

    order = place(session, settings, dex, jupiter, make_request(amount_usd="100", key="stale"))
    assert order.status == "rejected"
    assert order.reason is not None and "QUOTE_STALE" in order.reason
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 0


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
