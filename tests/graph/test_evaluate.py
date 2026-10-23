"""Evaluator semantics: Kleene logic, exec order, one order per tick, determinism."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from graph_helpers import (
    START,
    doc,
    edge,
    make_candles,
    make_ctx,
    make_snapshot,
    node,
    price_gate_doc,
    probe_doc,
)

from ezchain_core.graph import EvalContext, evaluate_graph, validate

T, F, U = "T", "F", "U"
TRUE, FALSE, UNKNOWN = True, False, None


def value_for(symbol: str) -> Decimal | None:
    return {T: Decimal(1), F: Decimal(-1), U: None}[symbol]


# -- Kleene logic ----------------------------------------------------------------------


def kleene_doc(operation: str) -> dict[str, Any]:
    """``var.get`` -> compare -> the logic node -> branch -> buy, so both the value and the
    exec consequence of the result are observable."""
    return doc(
        [
            node("tick", "event.tick"),
            node("a", "var.get", name="a"),
            node("b", "var.get", name="b"),
            node("cmp_a", "math.compare", op=">", b="0"),
            node("cmp_b", "math.compare", op=">", b="0"),
            node("gate", f"logic.{operation}"),
            node("br", "flow.branch"),
            node("buy", "act.buy", amount_usd="1"),
        ],
        [
            edge("a", "value", "cmp_a", "a"),
            edge("b", "value", "cmp_b", "a"),
            edge("cmp_a", "out", "gate", "a"),
            edge("cmp_b", "out", "gate", "b"),
            edge("tick", "out", "br", "in"),
            edge("gate", "out", "br", "cond"),
            edge("br", "true", "buy", "in"),
        ],
    )


AND_TABLE = [
    (T, T, TRUE),
    (T, F, FALSE),
    (T, U, UNKNOWN),
    (F, T, FALSE),
    (F, F, FALSE),
    (F, U, FALSE),
    (U, T, UNKNOWN),
    (U, F, FALSE),
    (U, U, UNKNOWN),
]

OR_TABLE = [
    (T, T, TRUE),
    (T, F, TRUE),
    (T, U, TRUE),
    (F, T, TRUE),
    (F, F, FALSE),
    (F, U, UNKNOWN),
    (U, T, TRUE),
    (U, F, UNKNOWN),
    (U, U, UNKNOWN),
]

NOT_TABLE = [(T, FALSE), (F, TRUE), (U, UNKNOWN)]


@pytest.mark.parametrize(("left", "right", "expected"), AND_TABLE)
def test_kleene_and(left: str, right: str, expected: bool | None) -> None:
    variables = {"a": value_for(left), "b": value_for(right)}
    decision = evaluate_graph(kleene_doc("and"), make_ctx(vars=variables))
    assert decision.trace["values"]["gate"]["out"] is expected
    assert (decision.action == "buy") is (expected is True)


@pytest.mark.parametrize(("left", "right", "expected"), OR_TABLE)
def test_kleene_or(left: str, right: str, expected: bool | None) -> None:
    variables = {"a": value_for(left), "b": value_for(right)}
    decision = evaluate_graph(kleene_doc("or"), make_ctx(vars=variables))
    assert decision.trace["values"]["gate"]["out"] is expected


@pytest.mark.parametrize(("operand", "expected"), NOT_TABLE)
def test_kleene_not(operand: str, expected: bool | None) -> None:
    raw = doc(
        [
            node("tick", "event.tick"),
            node("a", "var.get", name="a"),
            node("cmp", "math.compare", op=">", b="0"),
            node("gate", "logic.not"),
            node("br", "flow.branch"),
            node("buy", "act.buy", amount_usd="1"),
        ],
        [
            edge("a", "value", "cmp", "a"),
            edge("cmp", "out", "gate", "a"),
            edge("tick", "out", "br", "in"),
            edge("gate", "out", "br", "cond"),
            edge("br", "true", "buy", "in"),
        ],
    )
    decision = evaluate_graph(raw, make_ctx(vars={"a": value_for(operand)}))
    assert decision.trace["values"]["gate"]["out"] is expected


def test_unknown_condition_takes_neither_branch() -> None:
    raw = doc(
        [
            node("tick", "event.tick"),
            node("a", "var.get", name="missing"),
            node("cmp", "math.compare", op=">", b="0"),
            node("br", "flow.branch"),
            node("buy", "act.buy", amount_usd="1"),
            node("note", "act.notify", message="false path"),
        ],
        [
            edge("a", "value", "cmp", "a"),
            edge("cmp", "out", "br", "cond"),
            edge("tick", "out", "br", "in"),
            edge("br", "true", "buy", "in"),
            edge("br", "false", "note", "in"),
        ],
    )
    decision = evaluate_graph(raw, make_ctx())
    assert decision.action == "hold"
    assert decision.reason == "UNKNOWN_CONDITION"
    assert decision.effects == ()
    assert decision.trace["exec"] == ["tick", "br"]
    # The contract asks for the node id to be recorded with UNKNOWN_CONDITION.
    assert decision.trace["unknown"] == ["br"]


# -- cross_above / cross_below ---------------------------------------------------------


def cross_doc(node_type: str) -> dict[str, Any]:
    return doc(
        [
            node("tick", "event.tick"),
            node("fast", "data.candles", timeframe="5m"),
            node("slow", "data.candles", timeframe="1h"),
            node("cross", node_type),
            node("br", "flow.branch"),
            node("buy", "act.buy", amount_usd="1"),
        ],
        [
            edge("fast", "close", "cross", "a"),
            edge("slow", "close", "cross", "b"),
            edge("tick", "out", "br", "in"),
            edge("cross", "out", "br", "cond"),
            edge("br", "true", "buy", "in"),
        ],
    )


@pytest.mark.parametrize(
    ("a_closes", "b_closes", "above", "below"),
    [
        (["1", "2", "3"], ["2", "2", "2"], True, False),  # 2<=2 then 3>2
        (["3", "3", "3"], ["1", "1", "1"], False, False),  # already above
        (["3", "2", "1"], ["1", "2", "3"], False, True),  # 2>=2 then 1<3
        (["1", "1", "1"], ["3", "3", "3"], False, False),  # already below
        (["1", "2", "2"], ["2", "2", "2"], False, False),  # touches but does not cross
    ],
)
def test_cross_truth_table(
    a_closes: list[str], b_closes: list[str], above: bool, below: bool
) -> None:
    context = make_ctx(
        candles={
            "5m": make_candles(a_closes),
            "1h": make_candles(b_closes, timeframe_minutes=60),
        }
    )
    up = evaluate_graph(cross_doc("logic.cross_above"), context)
    down = evaluate_graph(cross_doc("logic.cross_below"), context)
    assert up.trace["values"]["cross"]["out"] is above
    assert down.trace["values"]["cross"]["out"] is below


def test_cross_is_unknown_with_a_short_series() -> None:
    context = make_ctx(
        candles={"5m": make_candles(["1"]), "1h": make_candles(["2"], timeframe_minutes=60)}
    )
    decision = evaluate_graph(cross_doc("logic.cross_above"), context)
    assert decision.trace["values"]["cross"]["out"] is None
    assert decision.reason == "UNKNOWN_CONDITION"


# -- one order per tick ----------------------------------------------------------------


def test_one_order_per_tick_keeps_the_first_and_records_the_rest() -> None:
    raw = doc(
        [
            node("tick", "event.tick"),
            node("seq", "flow.sequence"),
            node("first", "act.buy", amount_usd="100"),
            node("second", "act.buy", amount_usd="250"),
            node("third", "act.sell"),
        ],
        [
            edge("tick", "out", "seq", "in"),
            edge("seq", "then0", "first", "in"),
            edge("seq", "then1", "second", "in"),
            edge("seq", "then2", "third", "in"),
        ],
    )
    decision = evaluate_graph(raw, make_ctx())
    assert decision.action == "buy"
    assert decision.amount_usd == Decimal("100")
    assert decision.reason == "GRAPH_BUY"
    # The sell cannot fire while flat, so it records nothing rather than a suppression.
    assert [effect["kind"] for effect in decision.effects] == ["order", "suppressed_order"]
    assert decision.effects[0]["node"] == "first"
    assert decision.effects[1]["reason"] == "SUPPRESSED_ONE_ORDER_PER_TICK"
    assert decision.trace["exec"] == ["tick", "seq", "first", "second", "third"]


def test_effects_keep_exec_order() -> None:
    raw = doc(
        [
            node("tick", "event.tick"),
            node("seq", "flow.sequence"),
            node("note", "act.notify", message="entering", level="warn"),
            node("set", "act.set_var", name="entry"),
            node("price", "data.price"),
            node("buy", "act.buy", amount_usd="10"),
        ],
        [
            edge("tick", "out", "seq", "in"),
            edge("seq", "then0", "note", "in"),
            edge("price", "value", "set", "value"),
            edge("seq", "then1", "set", "in"),
            edge("seq", "then2", "buy", "in"),
        ],
    )
    decision = evaluate_graph(raw, make_ctx())
    assert [effect["kind"] for effect in decision.effects] == ["notify", "set_var", "order"]
    assert decision.effects[0]["level"] == "warn"
    assert decision.effects[1] == {
        "kind": "set_var",
        "name": "entry",
        "value": Decimal("1.00"),
        "node": "set",
    }


# -- sizing and clipping ---------------------------------------------------------------


def buy_doc(**params: Any) -> dict[str, Any]:
    return doc(
        [node("tick", "event.tick"), node("buy", "act.buy", **params)],
        [edge("tick", "out", "buy", "in")],
    )


def test_buy_is_clipped_to_the_position_cap() -> None:
    decision = evaluate_graph(
        buy_doc(amount_usd="1000"),
        make_ctx(cash_usd=Decimal("5000"), max_position_usd=Decimal("500")),
    )
    assert decision.amount_usd == Decimal("500")


def test_buy_uses_only_the_remaining_headroom() -> None:
    decision = evaluate_graph(
        buy_doc(amount_usd="1000"),
        make_ctx(
            snapshot=make_snapshot(price_usd=Decimal("2")),
            position_quantity=Decimal("240"),
            position_avg_cost_usd=Decimal("1.5"),
            max_position_usd=Decimal("500"),
        ),
    )
    # 240 * 2 = 480 spent; headroom is 20.
    assert decision.amount_usd == Decimal("20")


def test_buy_at_the_cap_holds() -> None:
    decision = evaluate_graph(
        buy_doc(amount_usd="100"),
        make_ctx(
            snapshot=make_snapshot(price_usd=Decimal("5")),
            position_quantity=Decimal("100"),
            position_avg_cost_usd=Decimal("4"),
            max_position_usd=Decimal("500"),
        ),
    )
    assert decision.action == "hold"
    assert decision.reason == "MAX_POSITION"
    assert decision.effects == ()


def test_buy_node_cap_overrides_the_context_cap() -> None:
    decision = evaluate_graph(
        buy_doc(amount_usd="1000", max_position_usd="150"),
        make_ctx(max_position_usd=Decimal("500")),
    )
    assert decision.amount_usd == Decimal("150")


def test_buy_pct_cash() -> None:
    decision = evaluate_graph(buy_doc(pct_cash="25"), make_ctx(cash_usd=Decimal("1000")))
    assert decision.amount_usd == Decimal("250")


def test_buy_pct_cash_with_unknown_cash_does_nothing() -> None:
    decision = evaluate_graph(buy_doc(pct_cash="25"), make_ctx(cash_usd=None))
    assert decision.action == "hold"
    assert decision.reason == "GRAPH_NO_ACTION"


def test_sell_fraction_and_mark_value() -> None:
    raw = doc(
        [node("tick", "event.tick"), node("sell", "act.sell", pct_position="40")],
        [edge("tick", "out", "sell", "in")],
    )
    decision = evaluate_graph(
        raw,
        make_ctx(
            snapshot=make_snapshot(price_usd=Decimal("2")),
            position_quantity=Decimal("10"),
            position_avg_cost_usd=Decimal("1"),
        ),
    )
    assert decision.action == "sell"
    assert decision.reason == "GRAPH_SELL"
    assert decision.sell_fraction == Decimal("0.4")
    assert decision.amount_usd == Decimal("8.0")


def test_sell_while_flat_holds() -> None:
    raw = doc(
        [node("tick", "event.tick"), node("sell", "act.sell")],
        [edge("tick", "out", "sell", "in")],
    )
    decision = evaluate_graph(raw, make_ctx())
    assert decision.action == "hold"
    assert decision.reason == "NO_POSITION"


def test_sell_defaults_to_the_whole_position() -> None:
    raw = doc(
        [node("tick", "event.tick"), node("sell", "act.sell")],
        [edge("tick", "out", "sell", "in")],
    )
    decision = evaluate_graph(raw, make_ctx(position_quantity=Decimal("3")))
    assert decision.sell_fraction == Decimal("1")


# -- guards ----------------------------------------------------------------------------


def trailing_doc() -> dict[str, Any]:
    return doc(
        [
            node("tick", "event.tick"),
            node("guard", "guard.trailing_stop", activate_pct="10", giveback_pct="5"),
            node("br", "flow.branch"),
            node("sell", "act.sell"),
        ],
        [
            edge("tick", "out", "br", "in"),
            edge("guard", "hit", "br", "cond"),
            edge("br", "true", "sell", "in"),
        ],
    )


@pytest.mark.parametrize(
    ("peak", "price", "armed", "hit"),
    [
        ("100", "100", False, False),
        ("105", "105", False, False),
        ("110", "110", True, False),
        ("110", "104", True, True),
        ("120", "113", True, True),
        ("120", "115", True, False),
    ],
)
def test_trailing_stop_arm_and_hit_sequence(peak: str, price: str, armed: bool, hit: bool) -> None:
    """arm at peak >= entry*1.10; hit at price <= peak*0.95 while armed."""
    decision = evaluate_graph(
        trailing_doc(),
        make_ctx(
            snapshot=make_snapshot(price_usd=Decimal(price)),
            position_quantity=Decimal("1"),
            position_avg_cost_usd=Decimal("100"),
            peak_since_entry_usd=Decimal(peak),
        ),
    )
    assert decision.trace["values"]["guard"] == {"armed": armed, "hit": hit}
    assert (decision.action == "sell") is hit


def test_trailing_stop_is_unknown_while_flat() -> None:
    decision = evaluate_graph(trailing_doc(), make_ctx())
    assert decision.trace["values"]["guard"] == {"armed": None, "hit": None}
    assert decision.reason == "UNKNOWN_CONDITION"


@pytest.mark.parametrize(
    ("node_type", "pct", "price", "expected"),
    [
        ("guard.take_profit", "20", "1.25", True),
        ("guard.take_profit", "20", "1.19", False),
        ("guard.take_profit", "20", "1.20", True),
        ("guard.stop_loss", "8", "0.91", True),
        ("guard.stop_loss", "8", "0.93", False),
        ("guard.stop_loss", "8", "0.92", True),
    ],
)
def test_take_profit_and_stop_loss(node_type: str, pct: str, price: str, expected: bool) -> None:
    raw = probe_doc(
        [node("tick", "event.tick"), node("guard", node_type, pct=pct)],
        [],
        source="guard",
        port="hit",
        kind="bool",
    )
    decision = evaluate_graph(
        raw,
        make_ctx(
            snapshot=make_snapshot(price_usd=Decimal(price)),
            position_quantity=Decimal("1"),
            position_avg_cost_usd=Decimal("1"),
        ),
    )
    assert decision.trace["values"]["guard"]["hit"] is expected


def test_guards_are_unknown_without_a_position() -> None:
    raw = probe_doc(
        [node("tick", "event.tick"), node("guard", "guard.take_profit", pct="20")],
        [],
        source="guard",
        port="hit",
        kind="bool",
    )
    decision = evaluate_graph(raw, make_ctx())
    assert decision.trace["values"]["guard"]["hit"] is None


# -- flow.gate_cooldown ----------------------------------------------------------------


def cooldown_doc() -> dict[str, Any]:
    return doc(
        [
            node("tick", "event.tick"),
            node("gate", "flow.gate_cooldown", minutes=30),
            node("buy", "act.buy", amount_usd="10"),
        ],
        [edge("tick", "out", "gate", "in"), edge("gate", "out", "buy", "in")],
    )


def test_cooldown_blocks_a_recent_fill() -> None:
    decision = evaluate_graph(cooldown_doc(), make_ctx(last_fill_at=START - timedelta(minutes=10)))
    assert decision.action == "hold"
    assert decision.trace["exec"] == ["tick", "gate"]


def test_cooldown_passes_an_old_fill() -> None:
    decision = evaluate_graph(cooldown_doc(), make_ctx(last_fill_at=START - timedelta(minutes=45)))
    assert decision.action == "buy"


def test_cooldown_passes_when_the_last_fill_is_unknown() -> None:
    decision = evaluate_graph(cooldown_doc(), make_ctx(last_fill_at=None))
    assert decision.action == "buy"


# -- state nodes -----------------------------------------------------------------------


def test_clock_is_utc_minutes_and_weekday() -> None:
    raw = probe_doc(
        [node("tick", "event.tick"), node("clock", "time.now")],
        [],
        source="clock",
        port="minute_of_day",
    )
    thursday_0930 = datetime(2026, 1, 1, 9, 30, tzinfo=UTC)
    decision = evaluate_graph(raw, make_ctx(now=thursday_0930))
    assert decision.trace["values"]["clock"] == {"minute_of_day": "570", "weekday": "3"}


def test_var_get_uses_the_default_until_set() -> None:
    raw = probe_doc(
        [node("tick", "event.tick"), node("v", "var.get", name="x", default="7")],
        [],
        source="v",
        port="value",
    )
    assert evaluate_graph(raw, make_ctx()).trace["values"]["v"]["value"] == "7"
    stored = evaluate_graph(raw, make_ctx(vars={"x": Decimal("9")}))
    assert stored.trace["values"]["v"]["value"] == "9"


def test_var_get_without_a_default_is_unknown() -> None:
    raw = probe_doc(
        [node("tick", "event.tick"), node("v", "var.get", name="x")],
        [],
        source="v",
        port="value",
    )
    assert evaluate_graph(raw, make_ctx()).trace["values"]["v"]["value"] is None


def test_position_state_is_unknown_when_flat() -> None:
    raw = probe_doc(
        [node("tick", "event.tick"), node("pos", "pos.state")],
        [],
        source="pos",
        port="quantity",
    )
    decision = evaluate_graph(raw, make_ctx())
    values = decision.trace["values"]["pos"]
    assert values["open"] is False
    assert values["quantity"] == "0"
    assert values["avg_cost_usd"] is None
    assert values["unrealized_pct"] is None
    assert values["minutes_in_position"] is None


def test_position_state_numbers() -> None:
    raw = probe_doc(
        [node("tick", "event.tick"), node("pos", "pos.state")],
        [],
        source="pos",
        port="quantity",
    )
    decision = evaluate_graph(
        raw,
        make_ctx(
            now=START + timedelta(minutes=90),
            snapshot=make_snapshot(price_usd=Decimal("1.2")),
            position_quantity=Decimal("100"),
            position_avg_cost_usd=Decimal("1"),
            entry_at=START,
            last_fill_at=START + timedelta(minutes=30),
            peak_since_entry_usd=Decimal("1.5"),
        ),
    )
    values = decision.trace["values"]["pos"]
    assert values["open"] is True
    assert values["value_usd"] == "120.0"
    assert values["unrealized_pct"] == "20.0"
    assert values["drawdown_from_peak_pct"] == "-20.0"
    assert values["minutes_in_position"] == "90"
    assert values["minutes_since_last_fill"] == "60"


# -- data nodes and the series cast ----------------------------------------------------


def test_stale_snapshot_makes_market_data_unknown() -> None:
    raw = probe_doc(
        [
            node("tick", "event.tick"),
            node("price", "data.price"),
            node("change", "data.change", window="1h"),
            node("liq", "data.liquidity"),
            node("age", "data.pair_age"),
            node("sum1", "math.add"),
            node("sum2", "math.add"),
            node("sum3", "math.add"),
        ],
        [
            edge("price", "value", "sum1", "a"),
            edge("change", "value", "sum1", "b"),
            edge("liq", "value", "sum2", "a"),
            edge("age", "value", "sum2", "b"),
            edge("sum1", "value", "sum3", "a"),
            edge("sum2", "value", "sum3", "b"),
        ],
        source="sum3",
        port="value",
    )
    decision = evaluate_graph(raw, make_ctx(snapshot=make_snapshot(stale=True)))
    for node_id in ("price", "change", "liq", "age"):
        assert decision.trace["values"][node_id]["value"] is None, node_id
    assert decision.trace["values"]["sum3"]["value"] is None


def test_pair_age_is_minutes() -> None:
    raw = probe_doc(
        [node("tick", "event.tick"), node("age", "data.pair_age")],
        [],
        source="age",
        port="value",
    )
    decision = evaluate_graph(
        raw,
        make_ctx(snapshot=make_snapshot(pair_created_at=START - timedelta(hours=2))),
    )
    assert decision.trace["values"]["age"]["value"] == "120"


def test_series_reads_its_last_value_at_a_number_input() -> None:
    raw = doc(
        [
            node("tick", "event.tick"),
            node("bars", "data.candles", timeframe="5m"),
            node("cmp", "math.compare", op=">", b="2"),
            node("br", "flow.branch"),
            node("buy", "act.buy", amount_usd="1"),
        ],
        [
            edge("bars", "close", "cmp", "a"),
            edge("tick", "out", "br", "in"),
            edge("cmp", "out", "br", "cond"),
            edge("br", "true", "buy", "in"),
        ],
    )
    decision = evaluate_graph(raw, make_ctx(candles={"5m": make_candles(["1", "2", "3"])}))
    assert decision.trace["values"]["cmp"]["out"] is True
    assert decision.action == "buy"


def test_empty_series_is_unknown_at_a_number_input() -> None:
    raw = probe_doc(
        [
            node("tick", "event.tick"),
            node("bars", "data.candles", timeframe="5m"),
            node("cmp", "math.compare", op=">", b="2"),
        ],
        [edge("bars", "close", "cmp", "a")],
        source="cmp",
        port="out",
        kind="bool",
    )
    decision = evaluate_graph(raw, make_ctx(candles={}))
    assert decision.trace["values"]["cmp"]["out"] is None


def test_unknown_volume_makes_the_volume_series_unknown() -> None:
    raw = probe_doc(
        [
            node("tick", "event.tick"),
            node("bars", "data.candles", timeframe="5m"),
            node("cmp", "math.compare", op=">", b="0"),
        ],
        [edge("bars", "close", "cmp", "a")],
        source="cmp",
        port="out",
        kind="bool",
    )
    bars = make_candles(["1", "2"], volumes=["10", None])
    decision = evaluate_graph(raw, make_ctx(candles={"5m": bars}))
    assert decision.trace["values"]["bars"]["volume"] is None
    assert decision.trace["values"]["bars"]["close"] == "2"


# -- arithmetic ------------------------------------------------------------------------


def test_division_by_zero_is_unknown() -> None:
    raw = probe_doc(
        [
            node("tick", "event.tick"),
            node("a", "math.const", value="1"),
            node("b", "math.const", value="0"),
            node("div", "math.div"),
        ],
        [edge("a", "value", "div", "a"), edge("b", "value", "div", "b")],
        source="div",
        port="value",
    )
    decision = evaluate_graph(raw, make_ctx())
    assert decision.trace["values"]["div"]["value"] is None


def test_between_is_inclusive() -> None:
    raw = probe_doc(
        [
            node("tick", "event.tick"),
            node("x", "math.const", value="5"),
            node("lo", "math.const", value="5"),
            node("hi", "math.const", value="10"),
            node("between", "math.between"),
        ],
        [
            edge("x", "value", "between", "x"),
            edge("lo", "value", "between", "lo"),
            edge("hi", "value", "between", "hi"),
        ],
        source="between",
        port="out",
        kind="bool",
    )
    assert evaluate_graph(raw, make_ctx()).trace["values"]["between"]["out"] is True


# -- determinism and invalid graphs ----------------------------------------------------


def test_decision_is_deterministic() -> None:
    raw = price_gate_doc()
    assert validate(raw).ok
    candles = {"5m": make_candles(["1.05", "0.95", "0.90", "1.00", "1.12"])}
    context = make_ctx(
        now=START + timedelta(minutes=25),
        candles=candles,
        snapshot=make_snapshot(price_usd=Decimal("1.12")),
        cash_usd=Decimal("1000"),
    )
    first = evaluate_graph(raw, context)
    second = evaluate_graph(raw, context)
    assert first.to_dict() == second.to_dict()
    assert first.effects == second.effects
    assert first.trace == second.trace


def test_decision_to_dict_is_json_safe() -> None:
    import json

    decision = evaluate_graph(
        price_gate_doc(),
        make_ctx(snapshot=make_snapshot(price_usd=Decimal("0.9"))),
    )
    payload = decision.to_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["action"] == "buy"
    assert payload["reason"] == "GRAPH_BUY"
    assert isinstance(payload["amount_usd"], str)


@pytest.mark.parametrize(
    "raw",
    [
        {"schema": "nope", "nodes": [], "edges": []},
        {"schema": "ezchain.graph/1", "nodes": [{"id": "tick", "type": "event.tick"}], "edges": []},
        "not even an object",
    ],
)
def test_invalid_documents_do_not_raise(raw: Any) -> None:
    decision = evaluate_graph(raw, make_ctx())
    assert decision.reason == "INVALID_GRAPH"
    assert decision.action == "hold"
    assert decision.trace == {"values": {}, "exec": [], "unknown": []}


def test_no_action_reason() -> None:
    # A valid graph (an order action is reachable) whose conditions simply do not fire.
    decision = evaluate_graph(
        price_gate_doc(), make_ctx(snapshot=make_snapshot(price_usd=Decimal("1.05")))
    )
    assert decision.action == "hold"
    assert decision.reason == "GRAPH_NO_ACTION"
    assert decision.effects == ()


def test_context_is_frozen() -> None:
    from dataclasses import FrozenInstanceError

    context = make_ctx()
    with pytest.raises(FrozenInstanceError):
        context.cash_usd = Decimal("1")  # type: ignore[misc]
    assert isinstance(context, EvalContext)
