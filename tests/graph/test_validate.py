"""Every validation code in contract section 6, each with a minimal failing document."""

from __future__ import annotations

from typing import Any

import pytest
from graph_helpers import doc, edge, node, price_gate_doc

from ezchain_core.graph import parse_document, validate
from ezchain_core.graph.validate import ERROR_CODES, WARNING_CODES, ValidationReport


def codes(report: ValidationReport) -> set[str]:
    return {issue.code for issue in report.errors}


def warn_codes(report: ValidationReport) -> set[str]:
    return {issue.code for issue in report.warnings}


def test_the_reference_graph_is_valid() -> None:
    report = validate(price_gate_doc())
    assert report.ok, report.errors
    assert report.errors == ()
    assert "NO_EXIT" not in warn_codes(report)


def test_validate_accepts_a_parsed_document() -> None:
    document = parse_document(price_gate_doc())
    assert validate(document).ok


def test_graph_schema() -> None:
    report = validate({"schema": "ezchain.graph/9", "nodes": [], "edges": []})
    assert not report.ok
    assert codes(report) == {"GRAPH_SCHEMA"}
    assert report.errors[0].node is None


def test_node_type_unknown() -> None:
    raw = doc(
        [node("tick", "event.tick"), node("ghost", "act.moon", amount_usd="1")],
        [edge("tick", "out", "ghost", "in")],
    )
    report = validate(raw)
    assert "NODE_TYPE_UNKNOWN" in codes(report)
    assert any(issue.node == "ghost" for issue in report.errors)


@pytest.mark.parametrize(
    ("params", "node_id"),
    [
        ({"amount_usd": "100", "typo": "1"}, "buy"),
        ({"amount_usd": "0"}, "buy"),
        ({"amount_usd": "100", "pct_cash": "10"}, "buy"),
        ({}, "buy"),
    ],
)
def test_param_invalid_on_actions(params: dict[str, Any], node_id: str) -> None:
    raw = price_gate_doc()
    for entry in raw["nodes"]:
        if entry["id"] == node_id:
            entry["params"] = params
    report = validate(raw)
    assert "PARAM_INVALID" in codes(report)
    assert any(issue.code == "PARAM_INVALID" and issue.node == node_id for issue in report.errors)


def test_param_invalid_missing_and_out_of_range() -> None:
    raw = doc(
        [node("tick", "event.tick"), node("bars", "data.candles", timeframe="5m")],
        [],
    )
    raw["nodes"].append(node("ema", "ind.ema", period=900))
    raw["edges"].append(edge("bars", "close", "ema", "series"))
    report = validate(raw)
    messages = [issue.message for issue in report.errors if issue.code == "PARAM_INVALID"]
    assert any("period" in message for message in messages)

    missing = doc([node("tick", "event.tick"), node("ema", "ind.ema")], [])
    missing["nodes"].append(node("bars", "data.candles", timeframe="5m"))
    missing["edges"].append(edge("bars", "close", "ema", "series"))
    assert "PARAM_INVALID" in codes(validate(missing))


def test_param_invalid_enum_and_text() -> None:
    raw = doc(
        [node("tick", "event.tick"), node("bars", "data.candles", timeframe="7m")],
        [],
    )
    assert "PARAM_INVALID" in codes(validate(raw))

    long_message = doc(
        [node("tick", "event.tick"), node("note", "act.notify", message="x" * 201)],
        [edge("tick", "out", "note", "in")],
    )
    assert "PARAM_INVALID" in codes(validate(long_message))


def test_param_invalid_macd_order() -> None:
    raw = doc(
        [
            node("tick", "event.tick"),
            node("bars", "data.candles", timeframe="5m"),
            node("macd", "ind.macd", fast=26, slow=12),
        ],
        [edge("bars", "close", "macd", "series")],
    )
    report = validate(raw)
    assert "PARAM_INVALID" in codes(report)
    assert any(issue.port == "fast" for issue in report.errors)


def test_port_unknown() -> None:
    raw = doc(
        [node("tick", "event.tick"), node("buy", "act.buy", amount_usd="1")],
        [edge("tick", "nope", "buy", "in")],
    )
    report = validate(raw)
    assert "PORT_UNKNOWN" in codes(report)
    assert any(issue.port == "nope" for issue in report.errors)

    unknown_input = doc(
        [node("tick", "event.tick"), node("buy", "act.buy", amount_usd="1")],
        [edge("tick", "out", "buy", "sideways")],
    )
    assert "PORT_UNKNOWN" in codes(validate(unknown_input))


def test_pin_type_mismatch_bool_into_number() -> None:
    raw = doc(
        [
            node("tick", "event.tick"),
            node("pos", "pos.state"),
            node("one", "math.const", value="1"),
            node("add", "math.add"),
            node("note", "act.notify", message="x"),
        ],
        [
            edge("tick", "out", "note", "in"),
            edge("pos", "open", "add", "a"),
            edge("one", "value", "add", "b"),
        ],
    )
    report = validate(raw)
    assert "PIN_TYPE_MISMATCH" in codes(report)


def test_series_into_number_is_the_only_implicit_cast() -> None:
    raw = doc(
        [
            node("tick", "event.tick"),
            node("bars", "data.candles", timeframe="5m"),
            node("one", "math.const", value="1"),
            node("cmp", "math.compare", op=">"),
            node("note", "act.notify", message="x"),
        ],
        [
            edge("tick", "out", "note", "in"),
            edge("bars", "close", "cmp", "a"),
            edge("one", "value", "cmp", "b"),
        ],
    )
    report = validate(raw)
    assert "PIN_TYPE_MISMATCH" not in codes(report)


def test_input_multiple() -> None:
    raw = doc(
        [
            node("tick", "event.tick"),
            node("one", "math.const", value="1"),
            node("two", "math.const", value="2"),
            node("add", "math.add"),
            node("note", "act.notify", message="x"),
        ],
        [
            edge("tick", "out", "note", "in"),
            edge("one", "value", "add", "a"),
            edge("two", "value", "add", "a"),
            edge("one", "value", "add", "b"),
        ],
    )
    report = validate(raw)
    assert "INPUT_MULTIPLE" in codes(report)
    assert any(issue.port == "a" for issue in report.errors)


def test_exec_fanout() -> None:
    raw = doc(
        [
            node("tick", "event.tick"),
            node("seq", "flow.sequence"),
            node("a", "act.notify", message="a"),
            node("b", "act.notify", message="b"),
        ],
        [
            edge("tick", "out", "seq", "in"),
            edge("seq", "then0", "a", "in"),
            edge("seq", "then0", "b", "in"),
        ],
    )
    report = validate(raw)
    assert "EXEC_FANOUT" in codes(report)
    assert any(issue.node == "seq" and issue.port == "then0" for issue in report.errors)


def test_input_required() -> None:
    raw = doc(
        [
            node("tick", "event.tick"),
            node("br", "flow.branch"),
            node("buy", "act.buy", amount_usd="1"),
        ],
        [edge("tick", "out", "br", "in"), edge("br", "true", "buy", "in")],
    )
    report = validate(raw)
    assert "INPUT_REQUIRED" in codes(report)
    assert any(issue.node == "br" and issue.port == "cond" for issue in report.errors)


def test_default_param_satisfies_a_required_input() -> None:
    """``math.compare.b`` is required but may fall back to its own param."""
    raw = doc(
        [
            node("tick", "event.tick"),
            node("price", "data.price"),
            node("cmp", "math.compare", op=">", b="1"),
            node("buy", "act.buy", amount_usd="1"),
            node("br", "flow.branch"),
        ],
        [
            edge("tick", "out", "br", "in"),
            edge("price", "value", "cmp", "a"),
            edge("cmp", "out", "br", "cond"),
            edge("br", "true", "buy", "in"),
        ],
    )
    report = validate(raw)
    assert "INPUT_REQUIRED" not in codes(report)


def test_cycle_in_data_wires() -> None:
    raw = doc(
        [
            node("tick", "event.tick"),
            node("one", "math.const", value="1"),
            node("a", "math.add"),
            node("b", "math.add"),
            node("note", "act.notify", message="x"),
        ],
        [
            edge("tick", "out", "note", "in"),
            edge("one", "value", "a", "a"),
            edge("a", "value", "b", "a"),
            edge("b", "value", "a", "b"),
            edge("one", "value", "b", "b"),
        ],
    )
    report = validate(raw)
    assert "CYCLE" in codes(report)


def test_cycle_in_exec_wires() -> None:
    raw = doc(
        [
            node("tick", "event.tick"),
            node("buy", "act.buy", amount_usd="1"),
            node("a", "flow.sequence"),
            node("b", "flow.sequence"),
        ],
        [
            edge("tick", "out", "buy", "in"),
            edge("a", "then0", "b", "in"),
            edge("b", "then0", "a", "in"),
        ],
    )
    report = validate(raw)
    assert "CYCLE" in codes(report)


def test_no_event() -> None:
    raw = doc([node("buy", "act.buy", amount_usd="1")], [])
    report = validate(raw)
    assert "NO_EVENT" in codes(report)


def test_no_action() -> None:
    raw = doc(
        [node("tick", "event.tick"), node("note", "act.notify", message="x")],
        [edge("tick", "out", "note", "in")],
    )
    report = validate(raw)
    assert "NO_ACTION" in codes(report)


def test_action_unreachable() -> None:
    raw = doc(
        [
            node("tick", "event.tick"),
            node("buy", "act.buy", amount_usd="1"),
            node("orphan_seq", "flow.sequence"),
            node("stray", "act.sell"),
        ],
        [
            edge("tick", "out", "buy", "in"),
            edge("orphan_seq", "then0", "stray", "in"),
        ],
    )
    report = validate(raw)
    assert "ACTION_UNREACHABLE" in codes(report)
    assert any(issue.node == "stray" for issue in report.errors)


def test_node_orphan_warning() -> None:
    raw = price_gate_doc()
    raw["nodes"].append(node("spare", "math.const", value="42"))
    report = validate(raw)
    assert report.ok
    assert "NODE_ORPHAN" in warn_codes(report)


def test_warmup_long_warning() -> None:
    raw = doc(
        [
            node("tick", "event.tick"),
            node("bars", "data.candles", timeframe="5m"),
            node("slow", "ind.ema", period=300),
            node("slower", "ind.ema", period=300),
            node("buy", "act.buy", amount_usd="1"),
        ],
        [
            edge("tick", "out", "buy", "in"),
            edge("bars", "close", "slow", "series"),
            edge("slow", "series", "slower", "series"),
        ],
    )
    report = validate(raw)
    assert "WARMUP_LONG" in warn_codes(report)
    assert report.warmup_bars["5m"] == 598


def test_no_exit_warning() -> None:
    raw = doc(
        [node("tick", "event.tick"), node("buy", "act.buy", amount_usd="1")],
        [edge("tick", "out", "buy", "in")],
    )
    report = validate(raw)
    assert "NO_EXIT" in warn_codes(report)


def test_duplicate_order_path_warning() -> None:
    raw = doc(
        [
            node("tick", "event.tick"),
            node("seq", "flow.sequence"),
            node("buy1", "act.buy", amount_usd="1"),
            node("buy2", "act.buy", amount_usd="2"),
        ],
        [
            edge("tick", "out", "seq", "in"),
            edge("seq", "then0", "buy1", "in"),
            edge("seq", "then1", "buy2", "in"),
        ],
    )
    report = validate(raw)
    assert "DUPLICATE_ORDER_PATH" in warn_codes(report)


def test_branch_orders_are_not_a_duplicate_path() -> None:
    """A buy on `true` and a sell on `false` are alternatives, not a duplicate path."""
    report = validate(price_gate_doc())
    assert "DUPLICATE_ORDER_PATH" not in warn_codes(report)


@pytest.mark.parametrize(
    ("node_type", "params", "expected"),
    [
        ("ind.sma", {"period": 20}, 19),
        ("ind.ema", {"period": 21}, 20),
        ("ind.rsi", {"period": 14}, 14),
        ("ind.bbands", {"period": 20, "stddev": "2"}, 19),
        ("ind.highest", {"period": 5}, 4),
        ("ind.lowest", {"period": 5}, 4),
        ("ind.roc", {"period": 3}, 3),
        ("ind.macd", {}, 33),
    ],
)
def test_warmup_bars_per_indicator(node_type: str, params: dict[str, Any], expected: int) -> None:
    raw = doc(
        [
            node("tick", "event.tick"),
            node("bars", "data.candles", timeframe="1h"),
            node("subject", node_type, **params),
            node("buy", "act.buy", amount_usd="1"),
        ],
        [
            edge("tick", "out", "buy", "in"),
            edge("bars", "close", "subject", "series"),
        ],
    )
    report = validate(raw)
    assert report.warmup_bars == {"1h": expected}


def test_warmup_bars_chain_and_timeframes() -> None:
    raw = doc(
        [
            node("tick", "event.tick"),
            node("m5", "data.candles", timeframe="5m"),
            node("h1", "data.candles", timeframe="1h"),
            node("ema", "ind.ema", period=10),
            node("ema2", "ind.ema", period=5),
            node("rsi", "ind.rsi", period=14),
            node("buy", "act.buy", amount_usd="1"),
        ],
        [
            edge("tick", "out", "buy", "in"),
            edge("m5", "close", "ema", "series"),
            edge("ema", "series", "ema2", "series"),
            edge("h1", "close", "rsi", "series"),
        ],
    )
    report = validate(raw)
    assert report.warmup_bars == {"5m": 13, "1h": 14}


def test_report_to_dict_is_json_shaped() -> None:
    report = validate(price_gate_doc())
    payload = report.to_dict()
    assert payload["ok"] is True
    assert payload["errors"] == []
    # This graph reads no candles, so no timeframe needs warmup bars.
    assert payload["warmup_bars"] == {}
    assert isinstance(payload["warnings"], list)


def test_every_documented_code_exists() -> None:
    assert {
        "GRAPH_SCHEMA",
        "NODE_TYPE_UNKNOWN",
        "PARAM_INVALID",
        "PORT_UNKNOWN",
        "PIN_TYPE_MISMATCH",
        "INPUT_MULTIPLE",
        "EXEC_FANOUT",
        "INPUT_REQUIRED",
        "CYCLE",
        "NO_EVENT",
        "NO_ACTION",
        "ACTION_UNREACHABLE",
    } == ERROR_CODES
    assert {"NODE_ORPHAN", "WARMUP_LONG", "NO_EXIT", "DUPLICATE_ORDER_PATH"} == WARNING_CODES
