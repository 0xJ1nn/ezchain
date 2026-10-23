"""Wire types round-trip through JSON without losing decimal precision."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from ezchain_sdk import (
    Account,
    Allocation,
    Candle,
    Intent,
    PluginContext,
    Position,
    Snapshot,
)


def test_context_round_trips() -> None:
    now = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    context = PluginContext(
        now=now,
        mode="paper",
        params={"lookback": 20},
        state={"last_side": "buy"},
        snapshot=Snapshot(
            chain="solana",
            address="So11111111111111111111111111111111111111112",
            symbol="SOL",
            name="Solana",
            price_usd=Decimal("150.25"),
        ),
        candles={
            "1h": (
                Candle(
                    open_time=now,
                    open=Decimal("149"),
                    high=Decimal("151"),
                    low=Decimal("148"),
                    close=Decimal("150.25"),
                    volume=None,
                ),
            )
        },
        position=Position(
            chain="solana",
            token_address="So11111111111111111111111111111111111111112",
            symbol="SOL",
            quantity=Decimal("2"),
            avg_cost_usd=Decimal("140"),
        ),
        account=Account(
            cash_usd=Decimal("1000"),
            equity_usd=Decimal("1300.50"),
            realized_usd=Decimal("0"),
            unrealized_usd=Decimal("20.50"),
            open_positions=1,
        ),
        allocation=Allocation(
            max_notional_usd=Decimal("250"),
            max_positions=3,
            max_daily_loss_usd=Decimal("50"),
        ),
    )

    decoded = PluginContext.from_dict(context.to_dict())

    assert decoded.now == now
    assert decoded.candles["1h"][0].close == Decimal("150.25")
    assert decoded.candles["1h"][0].volume is None
    assert decoded.position is not None
    assert decoded.position.avg_cost_usd == Decimal("140")
    assert decoded.account is not None
    assert decoded.account.equity_usd == Decimal("1300.50")
    assert decoded.allocation is not None
    assert decoded.allocation.max_notional_usd == Decimal("250")


def test_candles_for_returns_the_oldest_first_tail() -> None:
    now = datetime(2026, 10, 23, tzinfo=UTC)
    bars = tuple(
        Candle(
            open_time=now,
            open=Decimal(index),
            high=Decimal(index),
            low=Decimal(index),
            close=Decimal(index),
        )
        for index in range(5)
    )
    context = PluginContext(now=now, mode="paper", params={}, state={}, candles={"1h": bars})
    assert [bar.close for bar in context.candles_for("1h", 2)] == [Decimal(3), Decimal(4)]
    assert context.candles_for("5m", 2) == ()


def test_intent_rejects_a_non_positive_notional() -> None:
    with pytest.raises(ValueError):
        Intent(side="buy", notional_usd=Decimal("0"), reason="ENTRY")
    with pytest.raises(ValueError):
        Intent(side="buy", notional_usd=Decimal("1"), reason="")


def test_intent_round_trips_and_rejects_a_bad_side() -> None:
    intent = Intent(side="sell", notional_usd=Decimal("12.50"), reason="EXIT")
    assert Intent.from_dict(intent.to_dict()).notional_usd == Decimal("12.50")
    with pytest.raises(ValueError):
        Intent.from_dict({"side": "hold", "notional_usd": "1", "reason": "X"})


def test_context_random_is_deterministic_per_seed() -> None:
    now = datetime(2026, 10, 23, tzinfo=UTC)
    context = PluginContext(now=now, mode="paper", params={}, state={})
    first = [context.random("strategy").random() for _ in range(3)]
    second = [context.random("strategy").random() for _ in range(3)]
    assert first == second
