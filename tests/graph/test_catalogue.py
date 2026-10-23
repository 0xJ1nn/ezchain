"""Catalogue sanity: one table drives the palette, the validator and the evaluator."""

from __future__ import annotations

import json
from typing import Any

import pytest
from graph_helpers import doc, edge, make_candles, make_ctx, node

from ezchain_core.graph import (
    NODE_SPECS,
    NODE_TYPES,
    catalogue_json,
    evaluate_graph,
    get_node_spec,
    validate,
)
from ezchain_core.graph.catalogue import CATEGORY_ORDER, CATEGORY_TITLES, ParamSpec

#: Values for params that are required and have no default. Every such param must appear
#: here — the test below fails if the catalogue grows one without a sample.
SAMPLES: dict[str, dict[str, Any]] = {
    "data.change": {"window": "1h"},
    "data.candles": {"timeframe": "5m"},
    "ind.sma": {"period": 3},
    "ind.ema": {"period": 3},
    "ind.rsi": {"period": 14},
    "ind.bbands": {"period": 3, "stddev": "2"},
    "ind.atr": {"period": 3},
    "ind.highest": {"period": 3},
    "ind.lowest": {"period": 3},
    "ind.roc": {"period": 1},
    "ind.vwap": {"period": 3},
    "var.get": {"name": "entry"},
    "math.const": {"value": "1"},
    "math.compare": {"op": ">"},
    "flow.gate_cooldown": {"minutes": 30},
    "act.buy": {"amount_usd": "100"},
    "act.notify": {"message": "hello"},
    "act.set_var": {"name": "flag"},
    "guard.take_profit": {"pct": "10"},
    "guard.stop_loss": {"pct": "5"},
    "guard.trailing_stop": {"activate_pct": "10", "giveback_pct": "5"},
}

SOURCES: dict[str, tuple[str, str]] = {
    "exec": ("tick", "out"),
    "bool": ("flag", "out"),
    "number": ("one", "value"),
    "series": ("bars", "close"),
}


def source_nodes() -> list[dict[str, Any]]:
    return [
        node("tick", "event.tick"),
        node("bars", "data.candles", timeframe="5m"),
        node("one", "math.const", value="1"),
        node("zero", "math.const", value="0"),
        node("flag", "math.compare", op=">"),
    ]


def source_edges() -> list[dict[str, Any]]:
    return [edge("one", "value", "flag", "a"), edge("zero", "value", "flag", "b")]


def sample_params(node_type: str) -> dict[str, Any]:
    spec = get_node_spec(node_type)
    assert spec is not None
    params: dict[str, Any] = {}
    for entry in spec.params:
        if entry.default is not None:
            params[entry.name] = str(entry.default)
        elif entry.required:
            params[entry.name] = SAMPLES[node_type][entry.name]
    params.update(SAMPLES.get(node_type, {}))
    return params


def test_every_required_param_has_a_sample() -> None:
    for spec in NODE_SPECS:
        for entry in spec.params:
            if entry.required and entry.default is None:
                assert entry.name in SAMPLES.get(spec.type, {}), f"{spec.type}.{entry.name}"
        for key in SAMPLES.get(spec.type, {}):
            assert spec.param(key) is not None, f"{spec.type} has no param {key}"


@pytest.mark.parametrize("node_type", NODE_TYPES)
def test_node_type_accepts_its_own_defaults(node_type: str) -> None:
    """A node dropped on the canvas with catalogue defaults has no parameter or pin error."""
    spec = get_node_spec(node_type)
    assert spec is not None
    nodes = [*source_nodes(), node("subject", node_type, **sample_params(node_type))]
    edges = list(source_edges())
    for port in spec.inputs:
        if port.required:
            source_id, source_port = SOURCES[port.type]
            edges.append(edge(source_id, source_port, "subject", port.name))
    report = validate(doc(nodes, edges))
    for issue in report.errors:
        if issue.node != "subject":
            continue
        assert issue.code not in {
            "PARAM_INVALID",
            "PORT_UNKNOWN",
            "PIN_TYPE_MISMATCH",
            "INPUT_REQUIRED",
            "NODE_TYPE_UNKNOWN",
        }, f"{node_type}: {issue}"


def test_catalogue_json_is_stable_and_complete() -> None:
    first = catalogue_json()
    second = catalogue_json()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert json.loads(json.dumps(first)) == first

    assert first["schema"] == "ezchain.graph/1"
    assert [entry["type"] for entry in first["node_types"]] == list(NODE_TYPES)
    assert [entry["id"] for entry in first["categories"]] == list(CATEGORY_ORDER)

    seen_categories = set()
    for entry in first["node_types"]:
        seen_categories.add(entry["category"])
        assert entry["title"], entry["type"]
        assert entry["description"], entry["type"]
        assert entry["category"] in CATEGORY_TITLES
        for port in entry["inputs"]:
            assert port["type"] in {"exec", "bool", "number", "series"}
            assert isinstance(port["required"], bool)
        for port in entry["outputs"]:
            assert port["type"] in {"exec", "bool", "number", "series"}
    assert seen_categories == set(CATEGORY_ORDER)


def test_contract_node_types_are_all_present() -> None:
    """Section 5 of the contract lists these; the catalogue may not silently lose one."""
    expected = {
        "event.tick",
        "data.price",
        "data.change",
        "data.liquidity",
        "data.volume24h",
        "data.pair_age",
        "data.candles",
        "ind.sma",
        "ind.ema",
        "ind.rsi",
        "ind.bbands",
        "ind.atr",
        "ind.macd",
        "ind.highest",
        "ind.lowest",
        "ind.roc",
        "ind.vwap",
        "pos.state",
        "acct.state",
        "time.now",
        "var.get",
        "math.const",
        "math.add",
        "math.sub",
        "math.mul",
        "math.div",
        "math.min",
        "math.max",
        "math.abs",
        "math.compare",
        "math.between",
        "logic.and",
        "logic.or",
        "logic.not",
        "logic.cross_above",
        "logic.cross_below",
        "flow.branch",
        "flow.sequence",
        "flow.gate_cooldown",
        "act.buy",
        "act.sell",
        "act.notify",
        "act.set_var",
        "guard.take_profit",
        "guard.stop_loss",
        "guard.trailing_stop",
    }
    assert expected == set(NODE_TYPES)


def test_default_params_point_at_declared_params() -> None:
    for spec in NODE_SPECS:
        for port in spec.inputs:
            if port.default_param is None:
                continue
            entry = spec.param(port.default_param)
            assert entry is not None, f"{spec.type}.{port.name} -> {port.default_param}"
            assert entry.kind == "decimal"
            assert port.type == "number"


def test_no_duplicate_port_names() -> None:
    for spec in NODE_SPECS:
        inputs = [port.name for port in spec.inputs]
        outputs = [port.name for port in spec.outputs]
        assert len(inputs) == len(set(inputs)), spec.type
        assert len(outputs) == len(set(outputs)), spec.type
        # An input and an output may share a name (an indicator takes `series` and returns
        # `series`); wires are namespaced by direction, so that is unambiguous.


@pytest.mark.parametrize("node_type", NODE_TYPES)
def test_every_node_type_is_evaluated(node_type: str) -> None:
    """Behavioural proof: the evaluator produces a value (or visits) every node type."""
    spec = get_node_spec(node_type)
    assert spec is not None
    data_outputs = [port for port in spec.outputs if port.type != "exec"]
    has_exec_input = any(port.type == "exec" for port in spec.inputs)

    nodes = [
        *source_nodes(),
        node("seq", "flow.sequence"),
        node("notify", "act.notify", message="noop"),
        node("order", "act.buy", amount_usd="1"),
        node("sink_branch", "flow.branch"),
        node("sink_notify", "act.notify", message="noop"),
        node("subject", node_type, **sample_params(node_type)),
    ]
    edges = [
        *source_edges(),
        edge("tick", "out", "seq", "in"),
        edge("seq", "then0", "notify", "in"),
        edge("seq", "then2", "order", "in"),
        edge("seq", "then3", "sink_branch", "in"),
        edge("sink_branch", "true", "sink_notify", "in"),
        edge("flag", "out", "sink_branch", "cond"),
    ]
    for port in spec.inputs:
        if not port.required or port.type == "exec":
            continue
        source_id, source_port = SOURCES[port.type]
        edges.append(edge(source_id, source_port, "subject", port.name))
    if has_exec_input:
        edges.append(edge("seq", "then1", "subject", "in"))

    # Data nodes are lazy: give the subject's first output a consumer so it must be read.
    if data_outputs:
        first = data_outputs[0]
        if first.type == "bool":
            edges = [
                item
                for item in edges
                if not (item["to"]["node"] == "sink_branch" and item["to"]["port"] == "cond")
            ]
            edges.append(edge("subject", first.name, "sink_branch", "cond"))
        else:
            nodes.append(node("sink_cmp", "math.compare", op=">"))
            edges = [
                item
                for item in edges
                if not (item["to"]["node"] == "sink_branch" and item["to"]["port"] == "cond")
            ]
            edges.append(edge("subject", first.name, "sink_cmp", "a"))
            edges.append(edge("zero", "value", "sink_cmp", "b"))
            edges.append(edge("sink_cmp", "out", "sink_branch", "cond"))

    document = doc(nodes, edges)
    report = validate(document)
    assert report.ok, report.errors

    bars = make_candles(["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"])
    decision = evaluate_graph(document, make_ctx(candles={"5m": bars, "1h": bars}, vars={}))
    assert decision.reason != "INVALID_GRAPH"
    if node_type == "event.tick":
        assert "tick" in decision.trace["exec"]
        return
    if data_outputs:
        assert "subject" in decision.trace["values"], decision.trace
    if has_exec_input:
        assert "subject" in decision.trace["exec"], decision.trace


def test_param_spec_kinds_are_known() -> None:
    for spec in NODE_SPECS:
        for entry in spec.params:
            assert isinstance(entry, ParamSpec)
            assert entry.kind in {"decimal", "int", "enum", "str"}
            if entry.kind == "enum":
                assert entry.choices
            if entry.kind in {"decimal", "int"} and entry.min is not None and entry.max is not None:
                assert entry.min <= entry.max, f"{spec.type}.{entry.name}"
