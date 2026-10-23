"""Invariant checks are pure; these tests need no API, database or network."""

from __future__ import annotations

from decimal import Decimal

from tools.stress.checks import (
    check_account,
    check_chains,
    check_http,
    check_idempotency,
    check_latency,
    check_markets,
    check_orders_and_fills,
    check_positions,
    check_quote,
    check_reconciliation,
    check_strategy_run,
    check_strategy_run_orders,
    check_token,
    percentiles,
    self_test,
)


def order(
    order_id: str = "o1",
    *,
    status: str = "filled",
    side: str = "buy",
    amount: str = "1.00",
) -> dict[str, object]:
    return {"id": order_id, "status": status, "side": side, "amount_usd": amount}


def fill(fill_id: str = "f1", *, order_id: str = "o1") -> dict[str, object]:
    return {"id": fill_id, "order_id": order_id}


def position(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "chain": "base",
        "token_address": "0x00000000000000000000000000000000000000aa",
        "quantity": "2",
        "avg_cost_usd": "1",
        "mark_usd": "1.5",
        "market_value_usd": "3",
        "unrealized_usd": "1",
    }
    values.update(overrides)
    return values


def asset_row(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        **position(),
        "kind": "position",
        "share_pct": "3.00",
        "source": None,
        "mark_status": "known",
    }
    values.update(overrides)
    return values


def cash_row(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "chain": "settlement",
        "token_address": None,
        "symbol": "USD",
        "kind": "cash",
        "quantity": "97",
        "mark_usd": "1",
        "market_value_usd": "97",
        "share_pct": "97.00",
        "source": None,
        "mark_status": "known",
    }
    values.update(overrides)
    return values


def chain_row(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "chain": "base",
        "assets": 1,
        "unpriced_assets": 0,
        "market_value_usd": "3",
        "share_pct": "3.00",
        "mark_status": "known",
    }
    values.update(overrides)
    return values


def account(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "cash_usd": "97",
        "equity_usd": "100",
        "realized_usd": "0",
        "unrealized_usd": "1",
        "complete": True,
        "marks_unknown": False,
        "known_market_value_usd": "3",
        "unpriced_positions": [],
        "positions": [position()],
        "by_asset": [asset_row(), cash_row()],
        "by_chain": [
            chain_row(),
            chain_row(
                chain="settlement",
                market_value_usd="97",
                share_pct="97.00",
            ),
        ],
        "mode": "paper",
    }
    values.update(overrides)
    return values


def withheld_account(**overrides: object) -> dict[str, object]:
    """The same account with one open mark unknown, so equity and shares are withheld."""
    unknown = position(
        token_address="0x00000000000000000000000000000000000000bb",
        mark_usd=None,
        market_value_usd=None,
        unrealized_usd=None,
    )
    values = account(
        equity_usd=None,
        unrealized_usd=None,
        complete=False,
        marks_unknown=True,
        known_market_value_usd="3",
        positions=[position(), unknown],
        unpriced_positions=[
            {
                "chain": "base",
                "token_address": unknown["token_address"],
                "symbol": "TKN",
                "quantity": "2",
            }
        ],
        by_asset=[
            asset_row(),
            asset_row(
                token_address=unknown["token_address"],
                mark_usd=None,
                market_value_usd=None,
                unrealized_usd=None,
                share_pct=None,
                mark_status="unknown",
            ),
            cash_row(share_pct=None),
        ],
        by_chain=[
            chain_row(
                assets=2,
                unpriced_assets=1,
                market_value_usd=None,
                share_pct=None,
                mark_status="unknown",
            ),
            chain_row(chain="settlement", market_value_usd="97", share_pct=None),
        ],
    )
    values.update(overrides)
    return values


def test_clean_order_fill_position_account_snapshot() -> None:
    assert check_orders_and_fills([order()], [fill()], scenario="t") == []
    assert check_positions([position()], scenario="t") == []
    assert check_account(account(), scenario="t") == []


def test_fill_must_reference_an_existing_order() -> None:
    findings = check_orders_and_fills([], [fill(order_id="missing")], scenario="t")
    assert [finding.invariant for finding in findings] == ["orders.fill_order_exists"]
    assert findings[0].code == "FILL_ORDER_MISSING"


def test_filled_order_needs_exactly_one_fill() -> None:
    no_fill = check_orders_and_fills([order()], [], scenario="t")
    assert no_fill[0].invariant == "orders.filled_has_one_fill"
    assert no_fill[0].actual == "0"

    two_fills = check_orders_and_fills([order()], [fill("f1"), fill("f2")], scenario="t")
    assert two_fills[0].invariant == "orders.filled_has_one_fill"
    assert two_fills[0].actual == "2"


def test_non_filled_order_may_have_no_fill() -> None:
    assert check_orders_and_fills([order(status="rejected")], [], scenario="t") == []


def test_order_status_and_side_are_allowlisted() -> None:
    findings = check_orders_and_fills(
        [order(status="teleported", side="sideways")], [], scenario="t"
    )
    invariants = {finding.invariant for finding in findings}
    assert "orders.status_allowed" in invariants
    assert "orders.side_allowed" in invariants


def test_order_scope_ignores_rows_outside_the_run() -> None:
    # A shared account may contain old fills; only this run's ids are checked.
    findings = check_orders_and_fills([], [fill(order_id="old")], scenario="t", order_ids=set())
    assert findings == []


def test_idempotency_same_order_and_one_fill() -> None:
    first = order(status="filled")
    second = order(status="filled")
    assert check_idempotency(first, second, [fill()], scenario="t") == []

    different = check_idempotency(first, order("o2"), [], scenario="t")
    assert different[0].invariant == "idempotency.same_order"

    multiple = check_idempotency(first, second, [fill("f1"), fill("f2")], scenario="t")
    assert multiple[0].invariant == "idempotency.at_most_one_fill"


def test_position_null_marks_are_allowed_and_arithmetic_is_checked() -> None:
    unknown = position(mark_usd=None, market_value_usd=None, unrealized_usd=None)
    assert check_positions([unknown], scenario="t") == []

    bad_value = check_positions([position(market_value_usd="3.5")], scenario="t")
    assert bad_value[0].invariant == "positions.market_value_matches"

    bad_unrealized = check_positions([position(unrealized_usd="0.5")], scenario="t")
    assert bad_unrealized[0].invariant == "positions.unrealized_matches"

    zero = check_positions([position(quantity="0")], scenario="t")
    assert zero[0].invariant == "positions.quantity_positive"


def test_position_tolerance_accepts_rounding_noise() -> None:
    noisy = position(market_value_usd="3.004", unrealized_usd="1.004")
    assert check_positions([noisy], scenario="t", tolerance=Decimal("0.01")) == []


def test_account_rejects_non_finite_and_negative_cash() -> None:
    nan = check_account(account(cash_usd="NaN"), scenario="t")
    assert any(finding.code == "ACCOUNT_DECIMAL_INVALID" for finding in nan)
    infinite = check_account(account(equity_usd="Infinity"), scenario="t")
    assert any(finding.code == "ACCOUNT_DECIMAL_INVALID" for finding in infinite)
    negative = check_account(account(cash_usd="-1"), scenario="t")
    assert any(finding.invariant == "account.cash_non_negative" for finding in negative)


def test_account_equity_reconciles_with_known_market_values() -> None:
    findings = check_account(account(equity_usd="101"), scenario="t")
    assert findings[0].invariant == "account.equity_matches"
    assert findings[0].actual == "101"


def test_account_with_an_unknown_mark_withholds_equity() -> None:
    withheld = withheld_account()
    assert check_account(withheld, scenario="t") == []

    partial = check_account(withheld | {"equity_usd": "100"}, scenario="t")
    assert any(finding.code == "ACCOUNT_EQUITY_PARTIAL" for finding in partial)

    mislabelled = check_account(withheld | {"complete": True}, scenario="t")
    assert any(finding.code == "ACCOUNT_COMPLETE_MISMATCH" for finding in mislabelled)


def test_account_unpriced_is_defined_by_the_mark_not_the_market_value() -> None:
    # A null mark with a zero value is still unpriced: zero is not a substitute.
    zero_value = withheld_account(
        known_market_value_usd="0",
        positions=[position(mark_usd=None, market_value_usd="0", unrealized_usd=None)],
        by_asset=[
            asset_row(mark_usd=None, market_value_usd="0", share_pct=None, mark_status="unknown"),
            cash_row(share_pct=None),
        ],
        by_chain=[
            chain_row(
                assets=1,
                unpriced_assets=1,
                market_value_usd=None,
                share_pct=None,
                mark_status="unknown",
            )
        ],
        unpriced_positions=[
            {
                "chain": "base",
                "token_address": position()["token_address"],
                "symbol": "TKN",
                "quantity": "2",
            }
        ],
    )
    findings = check_account(zero_value, scenario="t")
    assert any(finding.code == "ACCOUNT_POSITION_VALUE_WITHOUT_MARK" for finding in findings)
    assert any(finding.code == "ACCOUNT_UNPRICED_ASSET_VALUED" for finding in findings)


def test_account_marks_unknown_must_match_complete() -> None:
    flipped = check_account(withheld_account(marks_unknown=False), scenario="t")
    assert any(finding.code == "ACCOUNT_MARKS_UNKNOWN_MISMATCH" for finding in flipped)
    assert any(finding.code == "ACCOUNT_MARKS_UNKNOWN_COMPLETE_MISMATCH" for finding in flipped)


def test_account_unpriced_positions_must_be_listed() -> None:
    missing = withheld_account()
    missing.pop("unpriced_positions")
    findings = check_account(missing, scenario="t")
    assert any(finding.code == "ACCOUNT_UNPRICED_POSITIONS_INVALID" for finding in findings)


def test_account_unpriced_asset_must_not_carry_a_source_or_value() -> None:
    leaked = withheld_account(
        by_asset=[
            asset_row(),
            asset_row(
                mark_usd=None,
                market_value_usd=None,
                unrealized_usd=None,
                share_pct="0.00",
                source={"name": "dexscreener", "observed_at": "2026-01-01T00:00:00Z"},
                mark_status="known",
            ),
            cash_row(share_pct=None),
        ]
    )
    findings = check_account(leaked, scenario="t")
    codes = {finding.code for finding in findings}
    assert "ACCOUNT_MARK_STATUS_MISMATCH" in codes
    assert "ACCOUNT_UNPRICED_ASSET_VALUED" in codes


def test_account_that_withholds_a_complete_equity_is_a_finding() -> None:
    findings = check_account(account(equity_usd=None, complete=True), scenario="t")
    assert any(finding.code == "ACCOUNT_EQUITY_WITHHELD" for finding in findings)


def test_reconciliation_404_is_a_failure() -> None:
    # The endpoint became mandatory: its absence means ledger truth is unproven.
    findings = check_reconciliation(404, None, scenario="t")
    assert [finding.invariant for finding in findings] == ["reconciliation.missing"]
    assert findings[0].code == "RECONCILIATION_MISSING"


def test_reconciliation_must_report_ok() -> None:
    assert check_reconciliation(200, {"ok": True}, scenario="t") == []
    assert check_reconciliation(200, {"status": "ok"}, scenario="t") == []
    failed = check_reconciliation(200, {"ok": False}, scenario="t")
    assert failed[0].invariant == "reconciliation.ok"
    assert check_reconciliation(500, {}, scenario="t")[0].code == "RECONCILIATION_STATUS"
    assert check_reconciliation(None, None, scenario="t")[0].code == "RECONCILIATION_UNREACHABLE"


def test_http_checks() -> None:
    assert check_http([200, 404], [], scenario="t") == []
    server = check_http([200, 503], [], scenario="t")
    assert server[0].invariant == "http.no_unexpected_5xx"
    transport = check_http([200], ["ReadError"], scenario="t")
    assert transport[0].invariant == "http.no_connection_errors"
    assert check_http([200], ["ReadError"], scenario="chaos", chaos=True) == []


def test_percentiles_use_nearest_rank() -> None:
    stats = percentiles([5.0, 1.0, 3.0, 2.0, 4.0])
    assert stats == {"count": 5.0, "p50": 3.0, "p95": 5.0, "p99": 5.0}
    assert percentiles([]) == {"count": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0}


def test_latency_budgets_report_breaches_per_group() -> None:
    within = check_latency(
        {"reads": [100.0], "paper_mutations": [200.0]},
        scenario="t",
        read_p95_ms=2000,
        mutation_p95_ms=1500,
        mutation_p99_ms=5000,
    )
    assert within == []

    breached = check_latency(
        {"reads": [2500.0], "paper_mutations": [1600.0, 6000.0]},
        scenario="t",
        read_p95_ms=2000,
        mutation_p95_ms=1500,
        mutation_p99_ms=5000,
    )
    invariants = {finding.invariant for finding in breached}
    assert invariants == {
        "latency.reads_p95",
        "latency.paper_mutations_p95",
        "latency.paper_mutations_p99",
    }


def test_market_shapes() -> None:
    valid = {
        "rows": [
            {
                "chain": "base",
                "pair_address": "0xpair",
                "base_token": "0x00000000000000000000000000000000000000aa",
                "symbol": "TEST",
                "price_usd": "1.23",
                "liquidity_usd": "50000",
                "source": {"name": "dexscreener", "observed_at": "2026-01-01T00:00:00Z"},
            }
        ],
        "source": {"name": "dexscreener", "observed_at": "2026-01-01T00:00:00Z"},
        "stale": False,
    }
    assert check_markets(valid, scenario="t") == []
    broken = {
        "rows": [
            {
                "chain": "",
                "pair_address": "p",
                "base_token": "t",
                "symbol": "S",
                "price_usd": "NaN",
                "liquidity_usd": "-1",
                "source": {},
            }
        ],
        "source": {},
        "stale": "no",
    }
    findings = check_markets(broken, scenario="t")
    assert findings
    assert all(finding.invariant == "markets.shape" for finding in findings)


def test_chain_token_and_quote_shapes() -> None:
    chain = {
        "id": "base",
        "name": "Base",
        "family": "evm",
        "native_asset": "ETH",
        "tier": "T1",
        "capabilities": {
            "browse": True,
            "paper_execute": True,
            "live_execute": False,
            "quote_kind": "paper_mark",
        },
    }
    assert check_chains([chain], scenario="t") == []
    no_paper = {**chain, "capabilities": {**chain["capabilities"], "paper_execute": False}}
    assert check_chains([no_paper], scenario="t")[0].code == "CHAIN_PAPER_CAPABILITY_MISSING"

    token = {
        "chain": "base",
        "address": "0x00000000000000000000000000000000000000aa",
        "symbol": "TEST",
        "name": "Test",
        "price_usd": "1.23",
        "pairs": [],
        "safety": {},
        "stale": False,
    }
    assert (
        check_token(
            token,
            scenario="t",
            chain="base",
            address="0x00000000000000000000000000000000000000aa",
        )
        == []
    )
    assert check_token(
        {**token, "chain": "solana"},
        scenario="t",
        chain="base",
        address="0x00000000000000000000000000000000000000aa",
    )

    quote = {
        "chain": "base",
        "token_in": "USDC",
        "token_out": "0x00000000000000000000000000000000000000aa",
        "side": "buy",
        "amount_in": "1",
        "amount_out": "2",
        "price": "0.5",
        "fee_usd": "0",
        "kind": "paper_mark",
        "source": {"name": "dexscreener", "observed_at": "2026-01-01T00:00:00Z"},
    }
    assert check_quote(quote, scenario="t") == []
    assert check_quote({**quote, "price": "0"}, scenario="t")


def test_market_unknown_liquidity_is_allowed_but_not_negative() -> None:
    payload = {
        "rows": [
            {
                "chain": "base",
                "pair_address": "0xpair",
                "base_token": "0xtoken",
                "symbol": "TKN",
                "price_usd": "1",
                "liquidity_usd": None,
                "source": {"name": "dexscreener", "observed_at": "2026-01-01T00:00:00+00:00"},
            }
        ],
        "source": {"name": "dexscreener", "observed_at": "2026-01-01T00:00:00+00:00"},
        "stale": False,
    }
    assert check_markets(payload, scenario="t") == []
    payload["rows"][0]["liquidity_usd"] = "-1"
    findings = check_markets(payload, scenario="t")
    assert [f.invariant for f in findings] == ["markets.shape"]


def test_strategy_run_claiming_a_trade_needs_a_filled_order() -> None:
    runs = [{"id": "r1", "action": "sell", "order_id": "o1"}]
    filled = [order("o1", status="filled")]
    assert check_strategy_run_orders(runs, filled, scenario="t") == []

    rejected = [order("o1", status="rejected")]
    findings = check_strategy_run_orders(runs, rejected, scenario="t")
    assert [f.invariant for f in findings] == ["strategy.run_order_filled"]
    assert findings[0].code == "STRATEGY_RUN_ORDER_NOT_FILLED"
    assert "rejected" in findings[0].actual

    missing = check_strategy_run_orders(runs, [], scenario="t")
    assert missing[0].code == "STRATEGY_RUN_ORDER_MISSING"


def test_strategy_hold_does_not_need_an_order() -> None:
    runs = [{"id": "r1", "action": "hold", "order_id": None}]
    assert check_strategy_run_orders(runs, [], scenario="t") == []


def test_strategy_run_log_must_not_be_empty() -> None:
    assert check_strategy_run([{"id": "r1"}], scenario="t") == []
    finding = check_strategy_run([], scenario="t")[0]
    assert finding.invariant == "strategy.run_log_non_empty"


def test_self_test_is_green() -> None:
    self_test()


def test_a_fill_older_than_the_truncated_order_page_is_not_a_missing_order() -> None:
    """Both pages are newest-first and capped; an older fill's order may be off-page."""
    orders = [
        {"id": "o2", "side": "buy", "status": "rejected", "created_at": "2026-01-01T00:02:00Z"}
    ]
    fills = [
        {
            "id": "f1",
            "order_id": "o1",
            "created_at": "2026-01-01T00:01:00+00:00",
        }
    ]
    assert check_orders_and_fills(orders, fills, scenario="t") == []


def test_a_fill_newer_than_the_order_page_must_have_its_order() -> None:
    orders = [
        {"id": "o2", "side": "buy", "status": "rejected", "created_at": "2026-01-01T00:02:00Z"}
    ]
    fills = [
        {
            "id": "f9",
            "order_id": "missing",
            "created_at": "2026-01-01T00:03:00+00:00",
        }
    ]
    findings = check_orders_and_fills(orders, fills, scenario="t")
    assert [finding.code for finding in findings] == ["FILL_ORDER_MISSING"]
