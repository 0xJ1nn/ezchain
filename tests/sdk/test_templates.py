"""The stablecoin template is a declarative starting graph, not a strategy plugin.

The template is a named, versioned graph document built only from catalogue nodes. These
checks prove the existing validator accepts it, the one evaluator produces paper decisions
from it, its version reaches the run log through a notify effect, and the same graph can be
backtested before it is armed.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from ezchain_core.graph import (
    NODE_TYPES,
    Candle,
    EvalContext,
    evaluate_graph,
    run_backtest,
    validate,
)
from ezchain_core.types import TokenSnapshot

TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2]
    / "packages/sdk-py/src/ezchain_sdk/templates/stablecoin.json"
)
RAW = TEMPLATE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def template() -> dict[str, Any]:
    return json.loads(RAW)


def snapshot(price: str) -> TokenSnapshot:
    return TokenSnapshot(
        chain="solana",
        address="So11111111111111111111111111111111111111112",
        symbol="USD",
        name="Stable",
        price_usd=Decimal(price),
        liquidity_usd=Decimal("50000"),
        volume_24h_usd=Decimal("25000"),
        price_change_m5=Decimal("0"),
        price_change_h1=Decimal("0"),
        price_change_h6=Decimal("0"),
        price_change_h24=Decimal("0"),
        stale=False,
    )


def context(price: str, *, quantity: str = "0", avg_cost: str | None = None) -> EvalContext:
    held = Decimal(quantity)
    value = held * Decimal(price)
    return EvalContext(
        now=datetime(2026, 1, 1, tzinfo=UTC),
        snapshot=snapshot(price),
        position_quantity=held,
        position_avg_cost_usd=None if avg_cost is None else Decimal(avg_cost),
        position_value_usd=value,
        cash_usd=Decimal("1000"),
        equity_usd=Decimal("1000") + value,
        max_position_usd=Decimal("500"),
    )


def version_note(decision: Any) -> str | None:
    """The notify message the engine persists in the run log's ``inputs.effects``."""
    for effect in decision.effects:
        if effect.get("kind") == "notify" and effect.get("node") == "version_note":
            message = effect.get("message")
            return message if isinstance(message, str) else None
    return None


def test_template_is_named_versioned_and_declares_its_gates(template: dict[str, Any]) -> None:
    assert template["name"] == "stablecoin"
    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", template["version"])
    assert template["arming"]["requires"] == ["backtest", "paper_forward"]
    assert set(template) <= {
        "schema",
        "name",
        "version",
        "description",
        "arming",
        "nodes",
        "edges",
        "comments",
    }


def test_template_contains_no_executable_strategy_code(template: dict[str, Any]) -> None:
    # Only the node catalogue is allowed to define behaviour; every node is a typed entry
    # in that catalogue, and the document carries no plugin entrypoint or code fields.
    types = {node["type"] for node in template["nodes"]}
    assert types <= set(NODE_TYPES)
    assert all(set(node) <= {"id", "type", "params", "ui"} for node in template["nodes"])
    assert not {"entrypoint", "sdk", "code", "source"} & set(template)
    assert not any(token in RAW for token in ("def ", "import ", "lambda ", "eval("))


def test_template_validates_with_the_existing_validator(template: dict[str, Any]) -> None:
    report = validate(template)
    assert report.ok
    assert report.errors == ()
    assert report.warnings == ()


def test_dry_run_buys_a_dip_and_records_the_template_version(
    template: dict[str, Any],
) -> None:
    decision = evaluate_graph(template, context("0.994"))
    assert decision.action == "buy"
    assert decision.reason == "GRAPH_BUY"
    assert str(decision.amount_usd) == "25"
    assert version_note(decision) == f"template=stablecoin version={template['version']}"
    assert "version_note" in decision.trace["exec"]


def test_dry_run_sells_a_recovery_and_records_the_template_version(
    template: dict[str, Any],
) -> None:
    decision = evaluate_graph(template, context("1.010", quantity="100", avg_cost="0.99"))
    assert decision.action == "sell"
    assert decision.reason == "GRAPH_SELL"
    assert str(decision.sell_fraction) == "1"
    assert version_note(decision) == f"template=stablecoin version={template['version']}"


def test_dry_run_holds_inside_the_band_but_still_records_the_version(
    template: dict[str, Any],
) -> None:
    decision = evaluate_graph(template, context("1.000"))
    assert decision.action == "hold"
    assert decision.reason == "GRAPH_NO_ACTION"
    assert version_note(decision) == f"template=stablecoin version={template['version']}"


def test_template_can_be_backtested_before_arming(template: dict[str, Any]) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    closes = ["1.000", "0.990", "0.992", "1.010", "1.012", "1.010"]
    bars = tuple(
        Candle(
            open_time=start + timedelta(minutes=5 * index),
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
            volume=Decimal("100"),
        )
        for index, close in enumerate(closes)
    )
    result = run_backtest(
        template,
        {"5m": bars},
        primary_timeframe="5m",
        fee_bps=0,
        slippage_bps=0,
        starting_cash_usd=Decimal("1000"),
        snapshot_template=snapshot("1.000"),
    )
    assert result.label == "BACKTEST"
    assert "backtest" in template["arming"]["requires"]
    assert [(trade.side, str(trade.price_usd)) for trade in result.trades] == [
        ("buy", "0.992"),
        ("sell", "1.012"),
    ]
    assert result.final_quantity == 0
