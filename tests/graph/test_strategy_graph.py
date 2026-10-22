"""The ``graph`` strategy kind on the existing strategy surface.

Only two things are new here: params parsing (which validates the document) and the
decision stub. The four existing kinds must behave exactly as before.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

import pytest
from graph_helpers import make_snapshot, node, price_gate_doc

from ezchain_core.graph import GraphDocument, validate
from ezchain_core.strategy import (
    GraphParams,
    MomentumParams,
    StrategySpec,
    StrategyValidationError,
    evaluate_strategy,
    interval_minutes,
    validate_params,
)
from ezchain_core.types import StrategyKind


def spec(kind: str, params: dict[str, Any] | None = None, status: str = "active") -> StrategySpec:
    return StrategySpec(
        id="s1",
        name="graph",
        chain="solana",
        token_address="So11111111111111111111111111111111111111112",
        token_symbol="TEST",
        kind=cast(StrategyKind, kind),
        params=params or {},
        status=status,
    )


def test_graph_is_a_strategy_kind() -> None:
    kinds = StrategyKind.__args__  # type: ignore[attr-defined]
    assert "graph" in kinds
    assert {"momentum", "dip", "dca", "threshold"} <= set(kinds)


def test_validate_params_parses_the_document() -> None:
    params = validate_params(
        "graph",
        {
            "graph": price_gate_doc(),
            "interval_minutes": 15,
            "max_position_usd": "250",
            "order_usd": "100",
        },
    )
    assert isinstance(params, GraphParams)
    assert isinstance(params.document, GraphDocument)
    assert params.interval_minutes == 15
    assert params.max_position_usd == Decimal("250")
    # order_usd is accepted (the shared column) but a graph sizes its own orders.
    assert params.order_usd == Decimal("100")
    assert [entry.id for entry in params.document.nodes][:2] == ["tick", "seq"]


def test_graph_defaults_apply() -> None:
    params = validate_params("graph", {"graph": price_gate_doc()})
    assert isinstance(params, GraphParams)
    assert params.interval_minutes == 60
    assert params.max_position_usd == Decimal("500")


def test_missing_graph_is_rejected() -> None:
    with pytest.raises(StrategyValidationError) as caught:
        validate_params("graph", {"interval_minutes": 5})
    assert caught.value.code == "STRATEGY_GRAPH_INVALID"


def test_invalid_document_is_rejected_with_its_codes() -> None:
    broken = {
        "schema": "ezchain.graph/1",
        "nodes": [{"id": "tick", "type": "event.tick", "params": {}}],
        "edges": [],
    }
    assert not validate(broken).ok
    with pytest.raises(StrategyValidationError) as caught:
        validate_params("graph", {"graph": broken})
    assert caught.value.code == "STRATEGY_GRAPH_INVALID"
    assert "NO_ACTION" in caught.value.message


def test_malformed_document_is_rejected() -> None:
    with pytest.raises(StrategyValidationError) as caught:
        validate_params("graph", {"graph": {"schema": "nope"}})
    assert caught.value.code == "STRATEGY_GRAPH_INVALID"


def test_unknown_keys_are_still_rejected() -> None:
    with pytest.raises(StrategyValidationError) as caught:
        validate_params("graph", {"graph": price_gate_doc(), "take_profit_pct": 10})
    assert caught.value.code == "STRATEGY_PARAM_UNKNOWN"


def test_graph_interval_minutes() -> None:
    params = {"graph": price_gate_doc(), "interval_minutes": 30}
    assert interval_minutes("graph", params) == 30
    assert interval_minutes("graph", params) == validate_params("graph", params).interval_minutes


def test_graph_interval_out_of_range_is_rejected() -> None:
    with pytest.raises(StrategyValidationError) as caught:
        validate_params("graph", {"graph": price_gate_doc(), "interval_minutes": 0})
    assert caught.value.code == "STRATEGY_PARAM_RANGE"


def test_evaluate_strategy_asks_for_context() -> None:
    decision = evaluate_strategy(
        spec("graph", {"graph": price_gate_doc()}),
        make_snapshot(),
    )
    assert decision.action == "hold"
    assert decision.reason == "GRAPH_NEEDS_CONTEXT"
    assert decision.amount_usd == Decimal(0)


def test_paused_graph_still_reports_paused() -> None:
    decision = evaluate_strategy(
        spec("graph", {"graph": price_gate_doc()}, status="paused"),
        make_snapshot(),
    )
    assert decision.reason == "PAUSED"


def test_typed_params_returns_the_document() -> None:
    typed = spec("graph", {"graph": price_gate_doc()}).typed_params
    assert isinstance(typed, GraphParams)
    assert typed.document.nodes[0].type == "event.tick"


def test_existing_kinds_are_unchanged() -> None:
    momentum = validate_params("momentum", {"lookback": "1h", "entry_change_pct": 5})
    assert isinstance(momentum, MomentumParams)
    assert momentum.take_profit_pct == Decimal("20")

    decision = evaluate_strategy(
        spec("momentum"),
        make_snapshot(price_change_h1=Decimal("6")),
    )
    assert decision.action == "buy"
    assert decision.reason == "MOMENTUM_ENTRY"

    dca = evaluate_strategy(spec("dca"), make_snapshot())
    assert dca.reason == "DCA_BUY"

    threshold = evaluate_strategy(
        spec("threshold", {"buy_below_usd": "2", "sell_above_usd": "3"}),
        make_snapshot(price_usd=Decimal("1.5")),
    )
    assert threshold.reason == "THRESHOLD_BUY"

    dip = evaluate_strategy(
        spec("dip"),
        make_snapshot(price_change_h1=Decimal("-9")),
    )
    assert dip.reason == "DIP_ENTRY"

    with pytest.raises(StrategyValidationError):
        validate_params("graph", {"graph": price_gate_doc(), "lookback": "1h"})


def test_document_with_a_bare_node_still_validates_for_the_palette() -> None:
    """A one-node document is a schema error, not a crash."""
    with pytest.raises(StrategyValidationError):
        validate_params(
            "graph",
            {
                "graph": {
                    "schema": "ezchain.graph/1",
                    "nodes": [node("t", "event.tick")],
                    "edges": [],
                }
            },
        )
