"""Independent ledger recompute: pure arithmetic, no API, no database."""

from __future__ import annotations

import random
from decimal import Decimal

import pytest
from tools.stress.ledger import (
    QUANTITY_TOLERANCE,
    SKIP_CONCURRENT_WRITER,
    SKIP_FILLS_WINDOW,
    check_ledger_recompute,
    foreign_fills,
    position_key,
    position_quantities,
    run_flow,
    summarize,
)

TOKEN = "0x00000000000000000000000000000000000000aa"
KEY = ("base", TOKEN)


def fill(
    fill_id: str,
    *,
    order_id: str,
    side: str,
    quantity: str,
    notional: str,
    fee: str = "0",
    token: str = TOKEN,
) -> dict[str, object]:
    return {
        "id": fill_id,
        "order_id": order_id,
        "chain": "base",
        "token_address": token,
        "side": side,
        "quantity": quantity,
        "notional_usd": notional,
        "fee_usd": fee,
    }


def position(quantity: str, *, token: str = TOKEN) -> dict[str, object]:
    return {"chain": "base", "token_address": token, "quantity": quantity}


def test_position_key_and_quantities_skip_unusable_rows() -> None:
    assert position_key({"chain": "base", "token_address": TOKEN}) == KEY
    assert position_key({"chain": "base"}) is None
    quantities = position_quantities(
        [
            position("2"),
            {"chain": "base", "token_address": TOKEN, "quantity": "1"},
            {"chain": "base", "quantity": "9"},
            {"chain": "base", "token_address": TOKEN, "quantity": "NaN"},
        ]
    )
    assert quantities == {KEY: Decimal("3")}


def test_run_flow_ignores_orders_this_run_did_not_create() -> None:
    fills = [
        fill("f1", order_id="ours-1", side="buy", quantity="5", notional="10", fee="0.03"),
        fill("f2", order_id="theirs", side="buy", quantity="100", notional="200"),
    ]
    flow = run_flow(fills, run_order_ids={"ours-1"})
    assert flow.bought == {KEY: Decimal("5")}
    assert flow.buy_notional == Decimal("10")
    assert flow.fill_count == 1
    assert flow.expected_cash_delta == Decimal("-10.03")


def test_recompute_matches_a_buy_then_a_partial_sell() -> None:
    fills = [
        fill("f1", order_id="o1", side="buy", quantity="5", notional="10", fee="0.03"),
        fill("f2", order_id="o2", side="sell", quantity="2", notional="4", fee="0.012"),
    ]
    verdict = check_ledger_recompute(
        scenario="t",
        before={},
        after={KEY: Decimal("3")},
        fills=fills,
        run_order_ids={"o1", "o2"},
        cash_before=Decimal("100"),
        cash_after=Decimal("93.958"),
    )
    assert verdict.findings == ()
    assert verdict.skip is None
    assert verdict.ok
    assert verdict.table[0]["buy_quantity"] == "5"
    assert verdict.table[0]["sell_quantity"] == "2"
    assert verdict.table[0]["observed_delta"] == "3"
    assert summarize(verdict)["findings"] == 0


def test_recompute_reports_a_position_delta_mismatch() -> None:
    fills = [fill("f1", order_id="o1", side="buy", quantity="5", notional="10")]
    verdict = check_ledger_recompute(
        scenario="t",
        before={},
        after={KEY: Decimal("4")},
        fills=fills,
        run_order_ids={"o1"},
        cash_before=None,
        cash_after=None,
    )
    assert [finding.invariant for finding in verdict.findings] == ["ledger.positions_match_fills"]
    assert verdict.findings[0].expected.startswith("5")
    assert verdict.findings[0].actual.startswith("4")


def test_recompute_reports_a_cash_delta_mismatch() -> None:
    fills = [fill("f1", order_id="o1", side="buy", quantity="5", notional="10", fee="0.03")]
    verdict = check_ledger_recompute(
        scenario="t",
        before={},
        after={KEY: Decimal("5")},
        fills=fills,
        run_order_ids={"o1"},
        cash_before=Decimal("100"),
        cash_after=Decimal("89"),
    )
    assert [finding.invariant for finding in verdict.findings] == ["ledger.cash_matches_fills"]


def test_cash_tolerance_is_one_cent() -> None:
    fills = [fill("f1", order_id="o1", side="buy", quantity="1", notional="10")]
    noisy = check_ledger_recompute(
        scenario="t",
        before={},
        after={KEY: Decimal("1")},
        fills=fills,
        run_order_ids={"o1"},
        cash_before=Decimal("100"),
        cash_after=Decimal("90.01"),
    )
    assert noisy.findings == ()
    breach = check_ledger_recompute(
        scenario="t",
        before={},
        after={KEY: Decimal("1")},
        fills=fills,
        run_order_ids={"o1"},
        cash_before=Decimal("100"),
        cash_after=Decimal("90.02"),
    )
    assert [finding.invariant for finding in breach.findings] == ["ledger.cash_matches_fills"]


def test_a_foreign_fill_in_the_window_skips_the_recompute() -> None:
    fills = [
        fill("f1", order_id="o1", side="buy", quantity="5", notional="10"),
        fill("f9", order_id="someone-else", side="sell", quantity="5", notional="10"),
    ]
    verdict = check_ledger_recompute(
        scenario="t",
        before={},
        after={KEY: Decimal("0")},
        fills=fills,
        run_order_ids={"o1"},
        cash_before=Decimal("100"),
        cash_after=Decimal("90"),
        new_fill_ids={"f1", "f9"},
    )
    assert verdict.skip == SKIP_CONCURRENT_WRITER
    assert verdict.findings == ()
    assert "someone-else" in verdict.detail


def test_an_old_fill_outside_the_window_is_not_a_concurrent_writer() -> None:
    fills = [
        fill("f1", order_id="o1", side="buy", quantity="5", notional="10"),
        fill("f9", order_id="someone-else", side="sell", quantity="5", notional="10"),
    ]
    assert foreign_fills(fills, run_order_ids={"o1"}, new_fill_ids={"f1"}) == [], (
        "a fill that was already there before the window is not a new writer"
    )


def test_a_filled_order_outside_the_fill_window_skips_the_recompute() -> None:
    verdict = check_ledger_recompute(
        scenario="t",
        before={},
        after={KEY: Decimal("0")},
        fills=[],
        run_order_ids={"o1"},
        cash_before=None,
        cash_after=None,
        filled_orders_missing_fill={"o1"},
    )
    assert verdict.skip == SKIP_FILLS_WINDOW
    assert verdict.findings == ()


@pytest.mark.parametrize(
    ("bought", "sold", "expected_delta"),
    [
        (Decimal("1"), Decimal("0"), Decimal("1")),
        (Decimal("0.1"), Decimal("0.05"), Decimal("0.05")),
        (Decimal("123.45678901"), Decimal("23.45678901"), Decimal("100")),
        (Decimal("1000000"), Decimal("999999.99999999"), Decimal("0.00000001")),
    ],
)
def test_delta_table(bought: Decimal, sold: Decimal, expected_delta: Decimal) -> None:
    fills = [
        fill("f1", order_id="o1", side="buy", quantity=str(bought), notional="1"),
        fill("f2", order_id="o2", side="sell", quantity=str(sold), notional="1"),
    ]
    verdict = check_ledger_recompute(
        scenario="t",
        before={KEY: Decimal("10")},
        after={KEY: Decimal("10") + expected_delta},
        fills=fills,
        run_order_ids={"o1", "o2"},
        cash_before=None,
        cash_after=None,
    )
    assert verdict.findings == ()


def test_seeded_random_flows_reconcile_and_perturbations_do_not() -> None:
    rng = random.Random(20261022)
    for _ in range(40):
        bought = Decimal(str(round(rng.uniform(0.5, 500), 6)))
        sold = Decimal(str(round(rng.uniform(0, float(bought)), 6)))
        buy_notional = Decimal(str(round(rng.uniform(1, 1000), 2)))
        sell_notional = Decimal(str(round(rng.uniform(0, float(buy_notional)), 2)))
        fee = Decimal(str(round(rng.uniform(0, 5), 4)))
        fills = [
            fill(
                "f1",
                order_id="o1",
                side="buy",
                quantity=str(bought),
                notional=str(buy_notional),
                fee=str(fee),
            ),
            fill("f2", order_id="o2", side="sell", quantity=str(sold), notional=str(sell_notional)),
        ]
        delta = bought - sold
        cash_delta = sell_notional - buy_notional - fee
        verdict = check_ledger_recompute(
            scenario="t",
            before={KEY: Decimal("0")},
            after={KEY: delta},
            fills=fills,
            run_order_ids={"o1", "o2"},
            cash_before=Decimal("1000"),
            cash_after=Decimal("1000") + cash_delta,
        )
        assert verdict.findings == ()
        assert abs(delta - (bought - sold)) <= QUANTITY_TOLERANCE

        perturbed = check_ledger_recompute(
            scenario="t",
            before={KEY: Decimal("0")},
            after={KEY: delta + Decimal("0.001")},
            fills=fills,
            run_order_ids={"o1", "o2"},
            cash_before=Decimal("1000"),
            cash_after=Decimal("1000") + cash_delta,
        )
        assert [finding.code for finding in perturbed.findings] == [
            "LEDGER_POSITION_DELTA_MISMATCH"
        ]
