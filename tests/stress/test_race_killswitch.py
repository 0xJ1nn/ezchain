"""Oversell race, kill-switch and position-close invariants (pure checks)."""

from __future__ import annotations

from decimal import Decimal

from tools.stress.checks import (
    check_killswitch_allows_manual_order,
    check_killswitch_run,
    check_killswitch_state,
    check_oversell,
    check_position_closed,
)
from tools.stress.scenarios import killswitch_params


def test_a_full_size_sell_of_the_whole_position_is_fine() -> None:
    assert (
        check_oversell(
            scenario="t",
            held_quantity=Decimal("2"),
            sold_quantity=Decimal("2"),
            final_quantity=None,
            cash_usd=Decimal("10"),
        )
        == []
    )


def test_selling_more_than_was_held_is_an_oversell() -> None:
    findings = check_oversell(
        scenario="t",
        held_quantity=Decimal("2"),
        sold_quantity=Decimal("6"),
        final_quantity=Decimal("-4"),
        cash_usd=Decimal("10"),
    )
    invariants = [finding.invariant for finding in findings]
    assert "race.no_oversell" in invariants
    assert "race.quantity_non_negative" in invariants
    assert findings[0].code == "RACE_OVERSELL"


def test_negative_position_and_cash_are_each_reported() -> None:
    findings = check_oversell(
        scenario="t",
        held_quantity=Decimal("2"),
        sold_quantity=Decimal("2"),
        final_quantity=Decimal("-1"),
        cash_usd=Decimal("-0.5"),
    )
    assert {finding.invariant for finding in findings} == {
        "race.quantity_non_negative",
        "race.cash_non_negative",
    }


def test_quantity_tolerance_absorbs_eight_decimal_dust() -> None:
    assert (
        check_oversell(
            scenario="t",
            held_quantity=Decimal("2"),
            sold_quantity=Decimal("2.000000001"),
            final_quantity=Decimal("0"),
        )
        == []
    )


def test_a_missing_final_position_is_zero_not_unknown() -> None:
    # A closed position disappears from the read model; that is 0, not a missing value.
    assert (
        check_oversell(
            scenario="t",
            held_quantity=Decimal("1"),
            sold_quantity=Decimal("1"),
            final_quantity=None,
        )
        == []
    )


def test_killswitch_block_is_proven_by_the_run_log() -> None:
    run = {"id": "r1", "action": "hold", "reason": "KILL_SWITCH_ACTIVE", "inputs": {}}
    assert check_killswitch_run(run, persisted_runs=[], orders_created=0, scenario="t") == []


def test_killswitch_block_may_be_proven_by_the_persisted_log_only() -> None:
    persisted = [
        {
            "id": "r1",
            "action": "hold",
            "reason": "KILL_SWITCH_ACTIVE",
            "inputs": {"error_code": "KILL_SWITCH_ACTIVE"},
        }
    ]
    assert (
        check_killswitch_run(None, persisted_runs=persisted, orders_created=0, scenario="t") == []
    )


def test_a_strategy_trade_while_armed_fails_the_scenario() -> None:
    run = {"id": "r1", "action": "buy", "reason": "THRESHOLD_BUY", "inputs": {}}
    findings = check_killswitch_run(run, persisted_runs=[], orders_created=1, scenario="t")
    assert {finding.invariant for finding in findings} == {"killswitch.blocks_strategy"}
    assert {finding.code for finding in findings} == {
        "KILLSWITCH_NOT_LOGGED",
        "KILLSWITCH_ORDER_PLACED",
    }


def test_killswitch_block_is_also_proven_by_the_order_service_guard() -> None:
    """The API's defence-in-depth rejection is the documented equivalent shape."""
    run = {
        "id": "r1",
        "action": "hold",
        "reason": "ORDER_REJECTED",
        "inputs": {"order_reason": "KILL_SWITCH_ACTIVE", "error_code": "ORDER_REJECTED"},
    }
    assert check_killswitch_run(run, persisted_runs=[], orders_created=0, scenario="t") == []


def test_a_hold_for_another_reason_does_not_prove_the_switch() -> None:
    run = {"id": "r1", "action": "hold", "reason": "THRESHOLD_WAIT", "inputs": {}}
    findings = check_killswitch_run(run, persisted_runs=[], orders_created=0, scenario="t")
    assert [finding.code for finding in findings] == ["KILLSWITCH_NOT_LOGGED"]


def test_manual_orders_must_stay_allowed() -> None:
    filled = {"id": "o1", "status": "filled", "reason": None}
    assert check_killswitch_allows_manual_order(filled, status_code=201, scenario="t") == []
    risk_rejected = {"id": "o2", "status": "rejected", "reason": "PAIR_AGE_UNKNOWN"}
    assert check_killswitch_allows_manual_order(risk_rejected, status_code=201, scenario="t") == []
    blocked = {"id": "o3", "status": "rejected", "reason": "KILL_SWITCH_ACTIVE"}
    findings = check_killswitch_allows_manual_order(blocked, status_code=201, scenario="t")
    assert [finding.code for finding in findings] == ["KILLSWITCH_MANUAL_ORDER_BLOCKED"]
    refused = check_killswitch_allows_manual_order(None, status_code=409, scenario="t")
    assert [finding.code for finding in refused] == ["KILLSWITCH_MANUAL_ORDER_REFUSED"]


def test_the_switch_state_is_checked_both_ways() -> None:
    assert check_killswitch_state({"active": True}, expected_active=True, scenario="t") == []
    assert (
        check_killswitch_state(
            {"active": False}, expected_active=False, scenario="t", restored=True
        )
        == []
    )
    not_armed = check_killswitch_state({"active": False}, expected_active=True, scenario="t")
    assert [finding.code for finding in not_armed] == ["KILLSWITCH_NOT_ARMED"]
    not_restored = check_killswitch_state(
        {"active": True}, expected_active=False, scenario="t", restored=True
    )
    assert [finding.code for finding in not_restored] == ["KILLSWITCH_NOT_RESTORED"]
    unreadable = check_killswitch_state(None, expected_active=False, scenario="t", restored=True)
    assert [finding.code for finding in unreadable] == ["KILLSWITCH_STATE_UNREADABLE"]


def test_position_close_accepts_dust_and_rejects_a_remainder() -> None:
    assert check_position_closed(Decimal("0"), scenario="t", label="base:0xaa") == []
    assert check_position_closed(None, scenario="t", label="base:0xaa") == []
    assert check_position_closed(Decimal("0.000000001"), scenario="t", label="base:0xaa") == []
    findings = check_position_closed(Decimal("0.5"), scenario="t", label="base:0xaa")
    assert [finding.code for finding in findings] == ["MATRIX_POSITION_NOT_CLOSED"]
    assert "base:0xaa" in findings[0].summary


def test_killswitch_params_want_a_buy_when_flat() -> None:
    params = killswitch_params(Decimal("100"), Decimal("1"), holding=False)
    buy_below = Decimal(str(params["buy_below_usd"]))
    sell_above = Decimal(str(params["sell_above_usd"]))
    # price <= buy_below triggers THRESHOLD_BUY, which reaches the switch guard.
    assert buy_below >= Decimal("100")
    assert sell_above > buy_below


def test_killswitch_params_want_a_sell_when_holding() -> None:
    params = killswitch_params(Decimal("100"), Decimal("1"), holding=True)
    buy_below = Decimal(str(params["buy_below_usd"]))
    sell_above = Decimal(str(params["sell_above_usd"]))
    # price >= sell_above triggers THRESHOLD_SELL, which also reaches the guard.
    assert sell_above <= Decimal("100")
    assert sell_above > buy_below
    assert Decimal(str(params["order_usd"])) == Decimal("1")


def test_killswitch_params_stay_valid_without_a_price() -> None:
    for holding in (True, False):
        params = killswitch_params(None, Decimal("2"), holding=holding)
        assert Decimal(str(params["buy_below_usd"])) > 0
        assert Decimal(str(params["sell_above_usd"])) > Decimal(str(params["buy_below_usd"]))
