"""Backtest harness: no look-ahead, next-bar fills, fee/slippage accounting, metrics."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from graph_helpers import (
    buy_once_doc,
    doc,
    edge,
    make_candles,
    make_snapshot,
    node,
    price_gate_doc,
)

from ezchain_core.graph import Candle, run_backtest, validate

STARTING_CASH = Decimal("1000")


def tolerance(value: Decimal, expected: str, places: str = "1e-6") -> bool:
    return abs(value - Decimal(expected)) <= Decimal(places)


def roc_buy_doc() -> dict[str, Any]:
    """Buys when the last closed bar closed above the one before it (roc(1) > 0)."""
    return doc(
        [
            node("tick", "event.tick"),
            node("bars", "data.candles", timeframe="5m"),
            node("roc", "ind.roc", period=1),
            node("zero", "math.const", value="0"),
            node("cmp", "math.compare", op=">"),
            node("pos", "pos.state"),
            node("flat", "logic.not"),
            node("ok", "logic.and"),
            node("br", "flow.branch"),
            node("buy", "act.buy", amount_usd="100"),
        ],
        [
            edge("bars", "close", "roc", "series"),
            edge("roc", "value", "cmp", "a"),
            edge("zero", "value", "cmp", "b"),
            edge("pos", "open", "flat", "a"),
            edge("cmp", "out", "ok", "a"),
            edge("flat", "out", "ok", "b"),
            edge("ok", "out", "br", "cond"),
            edge("tick", "out", "br", "in"),
            edge("br", "true", "buy", "in"),
        ],
    )


def run(
    document: dict[str, Any],
    bars: tuple[Candle, ...],
    *,
    fee_bps: int = 0,
    slippage_bps: int = 0,
    **kwargs: Any,
) -> Any:
    return run_backtest(
        document,
        {"5m": bars},
        primary_timeframe="5m",
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        starting_cash_usd=STARTING_CASH,
        snapshot_template=make_snapshot(price_usd=Decimal("1")),
        **kwargs,
    )


# -- no look-ahead ---------------------------------------------------------------------


def test_fills_at_the_next_bar_open_never_at_the_signal_close() -> None:
    bars = make_candles(
        ["1.00", "1.20", "1.85", "1.95"],
        opens=["1.00", "1.00", "1.80", "1.90"],
    )
    result = run(roc_buy_doc(), bars)
    assert len(result.trades) == 1
    trade = result.trades[0]
    # The signal came from bar 1 (close 1.20). The fill is bar 2's open, 1.80 — not 1.20,
    # and not bar 1's open either.
    assert trade.price_usd == Decimal("1.80")
    assert trade.time == bars[2].open_time
    assert trade.side == "buy"


def test_an_order_decided_on_the_last_bar_expires_unfilled() -> None:
    bars = make_candles(["1.00", "1.20"], opens=["1.00", "1.00"])
    result = run(roc_buy_doc(), bars)
    assert result.trades == ()
    assert result.final_equity_usd == STARTING_CASH


def test_the_context_never_sees_the_forming_bar() -> None:
    """A signal that needs a second bar cannot fire on the first bar."""
    bars = make_candles(["1.00", "1.20", "1.85"])
    result = run(roc_buy_doc(), bars)
    # roc(1) is unknown on bar 0 (no previous close), so nothing is decided there.
    assert result.trades[0].time == bars[2].open_time
    assert result.node_hits["br"] == 3


# -- a known scenario with exact arithmetic --------------------------------------------


TEN_BAR_CLOSES = ["1.05", "0.95", "0.90", "1.00", "1.12", "1.20", "1.05", "1.00", "1.05", "1.10"]


def test_known_ten_bar_scenario_has_the_exact_expected_pnl() -> None:
    """Buy 100 USD at 0.95, sell everything at 1.12, no fees and no slippage.

    qty = 100/0.95 = 2000/19; proceeds = 2000/19 * 1.12 = 2240/19; net = 340/19.
    """
    document = price_gate_doc(buy_below="1.00", sell_above="1.10")
    assert validate(document).ok
    result = run(document, make_candles(TEN_BAR_CLOSES))

    assert [trade.side for trade in result.trades] == ["buy", "sell"]
    assert result.trades[0].price_usd == Decimal("0.95")
    assert result.trades[1].price_usd == Decimal("1.12")
    assert tolerance(result.metrics.net_pnl_usd, "17.894737")
    assert tolerance(result.metrics.return_pct, "1.789474")
    assert tolerance(result.metrics.max_drawdown_pct, "0.526316")
    assert tolerance(result.final_cash_usd, "1017.894737")
    assert result.final_quantity == 0
    assert result.metrics.trades == 2
    assert result.metrics.closed_trades == 1
    assert result.metrics.win_rate_pct == Decimal("100.000000")
    # One winning trade and no losers: profit factor is undefined, never zero or infinity.
    assert result.metrics.profit_factor is None
    assert result.metrics.exposure_pct == Decimal("30.000000")
    assert result.metrics.sharpe is not None
    assert result.metrics.fees_usd == 0


# -- fees and slippage -----------------------------------------------------------------


def test_fee_and_slippage_accounting_reconciles() -> None:
    document = price_gate_doc(buy_below="1.00", sell_above="1.10")
    bars = make_candles(TEN_BAR_CLOSES)
    fee_bps, slippage_bps = 30, 20
    result = run(document, bars, fee_bps=fee_bps, slippage_bps=slippage_bps)

    fee_rate = Decimal(fee_bps) / Decimal(10_000)
    slippage = Decimal(slippage_bps) / Decimal(10_000)

    buy, sell = result.trades
    assert buy.price_usd == bars[2].open * (1 + slippage)
    assert sell.price_usd == bars[5].open * (1 - slippage)
    assert buy.fee_usd == buy.notional_usd * fee_rate
    assert sell.fee_usd == sell.notional_usd * fee_rate
    assert tolerance(result.metrics.fees_usd, str(buy.fee_usd + sell.fee_usd))

    # The books balance: cash + position value is the equity, and equity - start is the PnL.
    last_price = bars[-1].close
    position_value = result.final_quantity * last_price
    assert result.final_cash_usd + position_value == result.final_equity_usd
    assert tolerance(
        result.final_equity_usd - STARTING_CASH, str(result.metrics.net_pnl_usd), "1e-6"
    )
    assert result.equity_curve[-1].equity_usd == result.final_equity_usd
    assert result.equity_curve[-1].cash_usd == result.final_cash_usd

    # Fees make the round trip strictly worse than the frictionless version.
    frictionless = run(document, bars)
    assert result.metrics.net_pnl_usd < frictionless.metrics.net_pnl_usd


def test_cash_is_never_overspent() -> None:
    document = buy_once_doc(amount="5000")
    bars = make_candles(["1.00", "1.00", "1.00"])
    result = run(document, bars, fee_bps=100)
    assert result.final_cash_usd >= 0
    assert result.trades[0].notional_usd <= STARTING_CASH


def test_position_cap_is_honoured() -> None:
    document = buy_once_doc(amount="1000")
    result = run(document, make_candles(["1.00", "1.00"]), max_position_usd=Decimal("150"))
    assert result.trades[0].notional_usd == Decimal("150")


def test_partial_sell_keeps_a_remainder() -> None:
    document = doc(
        [
            node("tick", "event.tick"),
            node("seq", "flow.sequence"),
            node("price", "data.price"),
            node("pos", "pos.state"),
            node("below", "math.const", value="1.00"),
            node("cheap", "math.compare", op="<"),
            node("flat", "logic.not"),
            node("buy_ok", "logic.and"),
            node("above", "math.const", value="1.10"),
            node("rich", "math.compare", op=">"),
            node("sell_ok", "logic.and"),
            node("buy_branch", "flow.branch"),
            node("sell_branch", "flow.branch"),
            node("buy", "act.buy", amount_usd="100"),
            node("half", "act.sell", pct_position="50"),
        ],
        [
            edge("tick", "out", "seq", "in"),
            edge("seq", "then0", "buy_branch", "in"),
            edge("seq", "then1", "sell_branch", "in"),
            edge("price", "value", "cheap", "a"),
            edge("below", "value", "cheap", "b"),
            edge("pos", "open", "flat", "a"),
            edge("cheap", "out", "buy_ok", "a"),
            edge("flat", "out", "buy_ok", "b"),
            edge("buy_ok", "out", "buy_branch", "cond"),
            edge("buy_branch", "true", "buy", "in"),
            edge("price", "value", "rich", "a"),
            edge("above", "value", "rich", "b"),
            edge("rich", "out", "sell_ok", "a"),
            edge("pos", "open", "sell_ok", "b"),
            edge("sell_ok", "out", "sell_branch", "cond"),
            edge("sell_branch", "true", "half", "in"),
        ],
    )
    result = run(document, make_candles(["1.00", "0.90", "1.20", "1.30"]))
    # Buy at bar 1's close (0.90), half sold at bar 2's close (1.20); the last bar's sell
    # decision has no following bar to fill in.
    assert [trade.side for trade in result.trades] == ["buy", "sell"]
    assert result.trades[1].quantity == result.trades[0].quantity / 2
    assert result.final_quantity > 0
    marked = result.final_cash_usd + result.final_quantity * result.equity_curve[-1].price_usd
    assert tolerance(marked, str(result.final_equity_usd))


# -- metrics ---------------------------------------------------------------------------


def test_flat_graph_has_no_trades_and_no_metrics() -> None:
    document = doc(
        [node("tick", "event.tick"), node("buy", "act.buy", amount_usd="100")],
        [edge("tick", "out", "buy", "in")],
    )
    # A buy decided on the last bar never fills; a single-bar run also has < 2 returns.
    result = run(document, make_candles(["1.00"]))
    assert result.trades == ()
    assert result.metrics.net_pnl_usd == 0
    assert result.metrics.sharpe is None
    assert result.metrics.win_rate_pct is None
    assert result.metrics.profit_factor is None


def test_drawdown_is_measured_from_the_running_peak() -> None:
    document = buy_once_doc(below="1.05", amount="100")
    bars = make_candles(["1.00", "1.00", "2.00", "1.00"])
    result = run(document, bars)
    # Filled at 1.00 with 100 USD, peak equity 1100 at bar 2, then back to 1000 at bar 3.
    assert result.metrics.max_drawdown_pct > 0
    assert tolerance(result.metrics.max_drawdown_pct, "9.090909")


def test_equity_curve_has_one_point_per_bar() -> None:
    bars = make_candles(TEN_BAR_CLOSES)
    result = run(price_gate_doc(), bars)
    assert len(result.equity_curve) == len(bars)
    # Each point is stamped with its bar's *close* time (open + 5 minutes), in order.
    assert [point.time for point in result.equity_curve] == [
        bar.open_time + timedelta(minutes=5) for bar in bars
    ]
    assert result.equity_curve[0].equity_usd == STARTING_CASH


# -- result shape ----------------------------------------------------------------------


def test_result_declares_its_harness() -> None:
    result = run(price_gate_doc(), make_candles(TEN_BAR_CLOSES), fee_bps=25, slippage_bps=10)
    assert result.label == "BACKTEST"
    assert result.data_source == "provided_candles"
    assert result.fill_model == "next_bar_open"
    assert result.fee_bps == 25
    assert result.slippage_bps == 10
    payload = result.to_dict()
    assert payload["label"] == "BACKTEST"
    assert payload["fee_bps"] == 25
    assert payload["fill_model"] == "next_bar_open"
    import json

    assert json.loads(json.dumps(payload)) == payload


def test_backtest_is_deterministic() -> None:
    bars = make_candles(TEN_BAR_CLOSES)
    first = run(price_gate_doc(), bars, fee_bps=15, slippage_bps=5)
    second = run(price_gate_doc(), bars, fee_bps=15, slippage_bps=5)
    assert first.to_dict() == second.to_dict()


def test_node_hits_count_exec_visits() -> None:
    result = run(price_gate_doc(), make_candles(TEN_BAR_CLOSES))
    assert result.node_hits["tick"] == len(TEN_BAR_CLOSES)
    assert result.node_hits["seq"] == len(TEN_BAR_CLOSES)
    # The buy branch is visited every tick, even when its condition is false.
    assert result.node_hits["buy_branch"] == len(TEN_BAR_CLOSES)
    # The buy action is only visited on the tick that actually decided to buy.
    assert result.node_hits.get("buy", 0) == 1


def test_warmup_bars_are_reported() -> None:
    document = doc(
        [
            node("tick", "event.tick"),
            node("bars", "data.candles", timeframe="5m"),
            node("ema", "ind.ema", period=21),
            node("zero", "math.const", value="0"),
            node("cmp", "math.compare", op=">"),
            node("br", "flow.branch"),
            node("buy", "act.buy", amount_usd="10"),
        ],
        [
            edge("bars", "close", "ema", "series"),
            edge("ema", "value", "cmp", "a"),
            edge("zero", "value", "cmp", "b"),
            edge("cmp", "out", "br", "cond"),
            edge("tick", "out", "br", "in"),
            edge("br", "true", "buy", "in"),
        ],
    )
    result = run(document, make_candles(["1", "2", "3"]))
    assert result.warmup_bars == {"5m": 20}


# -- refusals --------------------------------------------------------------------------


def test_invalid_graph_is_refused() -> None:
    broken = doc([node("tick", "event.tick"), node("note", "act.notify", message="x")], [])
    with pytest.raises(ValueError, match="NO_ACTION"):
        run(broken, make_candles(["1"]))


def test_unknown_timeframe_is_refused() -> None:
    with pytest.raises(ValueError, match="unsupported timeframe"):
        run_backtest(
            price_gate_doc(),
            {"7m": make_candles(["1"])},
            primary_timeframe="5m",
            fee_bps=0,
            slippage_bps=0,
            starting_cash_usd=STARTING_CASH,
            snapshot_template=make_snapshot(),
        )


def test_negative_costs_and_zero_cash_are_refused() -> None:
    bars = make_candles(["1", "2"])
    with pytest.raises(ValueError):
        run(price_gate_doc(), bars, fee_bps=-1)
    with pytest.raises(ValueError):
        run(price_gate_doc(), bars, slippage_bps=-1)
    with pytest.raises(ValueError, match="starting_cash_usd"):
        run_backtest(
            price_gate_doc(),
            {"5m": bars},
            primary_timeframe="5m",
            fee_bps=0,
            slippage_bps=0,
            starting_cash_usd=Decimal(0),
            snapshot_template=make_snapshot(),
        )
