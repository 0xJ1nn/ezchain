"""Unit tests for the pure fail-closed risk policy."""

from __future__ import annotations

from datetime import timedelta, timezone
from decimal import Decimal

import pytest

from ezchain_core import (
    OrderRequest,
    Quote,
    RiskLimits,
    SourceRef,
    TokenSnapshot,
    evaluate_order,
    utcnow,
)

from .conftest import CHAIN, TOKEN, make_snapshot


def make_quote(
    side: str = "buy",
    *,
    amount_out: str = "0.05",
    kind: str = "paper_mark",
    observed_at=None,
    amount_in: str = "100",
) -> Quote:
    return Quote(
        chain=CHAIN,
        token_in=TOKEN if side == "sell" else "USDC",
        token_out="USDC" if side == "sell" else TOKEN,
        side="buy" if side == "buy" else "sell",
        amount_in=Decimal(amount_in),
        amount_out=Decimal(amount_out),
        price=Decimal("2000"),
        price_impact_pct=None,
        fee_usd=Decimal(0),
        kind="jupiter" if kind == "jupiter" else "paper_mark",
        route=None,
        source=SourceRef("dexscreener", None, observed_at or utcnow()),
    )


def request(side: str = "buy", amount: str = "100") -> OrderRequest:
    return OrderRequest(
        chain=CHAIN,
        token_address=TOKEN,
        side="buy" if side == "buy" else "sell",
        amount_usd=Decimal(amount),
        idempotency_key="risk-key-1",
    )


def evaluate(
    *,
    req: OrderRequest | None = None,
    snapshot: TokenSnapshot | None = None,
    quote: Quote | None = None,
    current_value: str = "0",
    open_positions: int = 0,
    daily_pnl: str = "0",
    limits: RiskLimits | None = None,
) -> tuple[bool, tuple[str, ...]]:
    return evaluate_order(
        request=req or request(),
        snapshot=snapshot or make_snapshot(),
        quote=quote or make_quote(),
        current_position_value_usd=Decimal(current_value),
        open_positions=open_positions,
        daily_realized_pnl_usd=Decimal(daily_pnl),
        limits=limits,
    )


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_unknown_price_denies(side: str) -> None:
    allowed, reasons = evaluate(
        req=request(side=side),
        quote=make_quote(side=side),
        snapshot=make_snapshot(price=None),
    )
    assert allowed is False
    assert "PRICE_UNKNOWN" in reasons


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_unknown_liquidity_denies(side: str) -> None:
    allowed, reasons = evaluate(
        req=request(side=side),
        quote=make_quote(side=side),
        snapshot=make_snapshot(liquidity=None),
    )
    assert allowed is False
    assert "LIQUIDITY_UNKNOWN" in reasons


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_low_liquidity_denies(side: str) -> None:
    allowed, reasons = evaluate(
        req=request(side=side),
        quote=make_quote(side=side),
        snapshot=make_snapshot(liquidity=Decimal("9999")),
    )
    assert allowed is False
    assert "LIQUIDITY_TOO_LOW" in reasons


def test_buy_with_unknown_pair_age_denies() -> None:
    allowed, reasons = evaluate(snapshot=make_snapshot(pair_created_at=None))
    assert allowed is False
    assert "PAIR_AGE_UNKNOWN" in reasons


def test_sell_with_unknown_pair_age_is_allowed() -> None:
    quote = make_quote(side="sell", amount_out="100", amount_in="100", kind="jupiter")
    allowed, reasons = evaluate(
        req=request(side="sell"),
        quote=quote,
        snapshot=make_snapshot(pair_created_at=None),
    )
    assert allowed is True
    assert reasons == ()


def test_young_pair_is_denied_for_a_buy() -> None:
    allowed, reasons = evaluate(
        snapshot=make_snapshot(pair_created_at=utcnow() - timedelta(minutes=5))
    )
    assert allowed is False
    assert "PAIR_TOO_YOUNG" in reasons


def test_old_pair_is_allowed_for_a_buy() -> None:
    allowed, reasons = evaluate(
        snapshot=make_snapshot(pair_created_at=utcnow() - timedelta(hours=1))
    )
    assert allowed is True
    assert reasons == ()


def test_naive_pair_created_at_is_treated_as_utc() -> None:
    observed = utcnow()
    allowed, reasons = evaluate(
        snapshot=make_snapshot(
            observed_at=observed,
            pair_created_at=(observed - timedelta(hours=1)).replace(tzinfo=None),
        )
    )
    assert allowed is True
    assert reasons == ()


def test_non_utc_pair_created_at_uses_the_same_instant() -> None:
    observed = utcnow()
    offset = timezone(timedelta(hours=5, minutes=30))
    allowed, reasons = evaluate(
        snapshot=make_snapshot(
            observed_at=observed,
            pair_created_at=(observed - timedelta(hours=1)).astimezone(offset),
        )
    )
    assert allowed is True
    assert reasons == ()


def test_young_pair_does_not_block_a_sell() -> None:
    quote = make_quote(side="sell", amount_out="100", amount_in="100", kind="jupiter")
    allowed, reasons = evaluate(
        req=request(side="sell"),
        quote=quote,
        snapshot=make_snapshot(pair_created_at=utcnow() - timedelta(minutes=5)),
    )
    assert allowed is True
    assert reasons == ()


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_stale_quote_denies(side: str) -> None:
    from datetime import timedelta

    stale = utcnow() - timedelta(seconds=61)
    allowed, reasons = evaluate(
        req=request(side=side), quote=make_quote(side=side, observed_at=stale)
    )
    assert allowed is False
    assert reasons == ("QUOTE_STALE",)


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_stale_snapshot_denies(side: str) -> None:
    allowed, reasons = evaluate(
        req=request(side=side),
        quote=make_quote(side=side),
        snapshot=make_snapshot(stale=True),
    )
    assert allowed is False
    assert "SNAPSHOT_STALE" in reasons


def test_order_size_denies() -> None:
    allowed, reasons = evaluate(req=request(amount="1000.01"))
    assert allowed is False
    assert "ORDER_LIMIT" in reasons


def test_paper_mark_size_cap_denies() -> None:
    allowed, reasons = evaluate(req=request(amount="250.01"))
    assert allowed is False
    assert "PAPER_MARK_LIMIT" in reasons


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_missing_pairs_denies(side: str) -> None:
    allowed, reasons = evaluate(
        req=request(side=side),
        quote=make_quote(side=side),
        snapshot=make_snapshot(pairs=0),
    )
    assert allowed is False
    assert "NO_PAIR" in reasons


def test_position_limit_denies_a_buy() -> None:
    allowed, reasons = evaluate(req=request(amount="200"), current_value="2400")
    assert allowed is False
    assert "POSITION_LIMIT" in reasons


def test_position_limit_never_blocks_a_sell() -> None:
    """Reducing risk must stay possible: plan §Paper accounting / risk intent."""
    quote = make_quote(side="sell", amount_out="2400", amount_in="1.2", kind="jupiter")
    allowed, reasons = evaluate(
        req=request(side="sell", amount="2400"),
        quote=quote,
        current_value="2400",
        open_positions=10,
    )
    assert allowed is True
    assert reasons == ()


def test_order_size_never_blocks_a_sell() -> None:
    """A position larger than max_order_usd must still be exitable in one order."""
    quote = make_quote(side="sell", amount_out="2400", amount_in="1.2", kind="jupiter")
    allowed, reasons = evaluate(
        req=request(side="sell", amount="2400"),
        quote=quote,
        current_value="2400",
        limits=RiskLimits(max_order_usd=Decimal("1000")),
    )
    assert allowed is True
    assert reasons == ()


def test_paper_mark_cap_never_blocks_a_sell() -> None:
    """The paper-mark order cap sizes new risk; it must not trap an exit."""
    quote = make_quote(side="sell", amount_out="300", amount_in="300", kind="paper_mark")
    allowed, reasons = evaluate(
        req=request(side="sell", amount="300"),
        quote=quote,
        current_value="300",
        limits=RiskLimits(max_order_usd=Decimal("1000"), paper_mark_max_order_usd=Decimal("250")),
    )
    assert allowed is True
    assert reasons == ()


def test_daily_loss_never_blocks_a_sell() -> None:
    """A loss breach stops new risk; it must not stop the exit that reduces it."""
    quote = make_quote(side="sell", amount_out="100", amount_in="100", kind="jupiter")
    allowed, reasons = evaluate(
        req=request(side="sell", amount="100"),
        quote=quote,
        daily_pnl="-10000",
        limits=RiskLimits(max_daily_loss_usd=Decimal("500")),
    )
    assert allowed is True
    assert reasons == ()


def test_max_open_positions_denies_a_new_buy() -> None:
    allowed, reasons = evaluate(open_positions=10)
    assert allowed is False
    assert "MAX_OPEN_POSITIONS" in reasons


def test_daily_loss_limit_denies() -> None:
    allowed, reasons = evaluate(daily_pnl="-500")
    assert allowed is False
    assert "DAILY_LOSS_LIMIT" in reasons


def test_sell_without_route_denies() -> None:
    allowed, reasons = evaluate(
        req=request(side="sell", amount="100"),
        quote=make_quote(side="sell", amount_out="0"),
    )
    assert allowed is False
    assert "NO_SELL_ROUTE" in reasons
