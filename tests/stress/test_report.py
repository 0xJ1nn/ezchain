"""Report rendering: every mode leaves readable evidence in the Markdown summary."""

from __future__ import annotations

from tools.stress.report import render_markdown


def report(**sections: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "mode": "all",
        "status": "pass",
        "seed": 0,
        "concurrency": 4,
        "base_url": "http://127.0.0.1:8011",
        "elapsed_s": 12.5,
        "command": "python -m tools.stress --mode all",
        "git_commit": "abc123",
        "git_dirty": True,
        "requests": {"total": 10, "by_status": {"200": 10}, "by_group": {"reads": {"total": 10}}},
        "latency_ms": {"reads": {"count": 10, "p50": 1, "p95": 2, "p99": 3}},
        "invariants": [
            {"id": "reconciliation.ok", "status": "pass", "detail": "ok"},
            {"id": "ledger.positions_match_fills", "status": "skip", "detail": "endpoint absent"},
        ],
        "created_ids": {"orders": ["o1"], "strategies": [], "deleted_strategies": []},
        "findings_total": 0,
        "findings_occurrences": 0,
        "failure_samples": [],
        "skips": ["ledger recompute skipped: concurrent_writer_suspected"],
        "safety": {"limits": ["no keys"]},
        "sections": sections,
    }
    return payload


def test_matrix_table_has_the_required_columns() -> None:
    markdown = render_markdown(
        report(
            matrix=[
                {
                    "chain": "base",
                    "token": "0xaa",
                    "symbol": "WETH",
                    "quote_kind": "paper_mark",
                    "buy": "filled",
                    "sell": "filled",
                    "status": "ok",
                    "reason": "",
                },
                {
                    "chain": "near",
                    "token": "",
                    "symbol": "",
                    "quote_kind": "paper_mark",
                    "buy": "not_attempted",
                    "sell": "not_attempted",
                    "status": "no_data",
                    "reason": "no market rows",
                },
                {
                    "chain": "ton",
                    "token": "0xbb",
                    "symbol": "TON",
                    "quote_kind": "paper_mark",
                    "buy": "rejected",
                    "sell": "skipped",
                    "status": "rejected:PAIR_TOO_YOUNG",
                    "reason": "PAIR_TOO_YOUNG",
                },
            ]
        )
    )
    assert "## Chain matrix" in markdown
    assert "| chain | token | quote_kind | buy | sell | status | reason |" in markdown
    assert "| base | WETH | paper_mark | filled | filled | ok | - |" in markdown
    assert "| ton | TON | paper_mark | rejected | skipped | rejected:PAIR_TOO_YOUNG" in markdown
    assert "Totals: no_data=1, ok=1, rejected:PAIR_TOO_YOUNG=1" in markdown


def test_ledger_section_shows_the_recompute() -> None:
    markdown = render_markdown(
        report(
            ledger=[
                {
                    "detail": "2 fill(s) across 1 token(s); expected cash delta -1.5",
                    "skip": None,
                    "cash_before": "10000",
                    "cash_after": "9998.5",
                    "tokens": [
                        {
                            "chain": "base",
                            "token_address": "0xaa",
                            "buy_quantity": "2",
                            "sell_quantity": "0",
                            "expected_delta": "2",
                            "observed_delta": "2",
                            "ok": True,
                        }
                    ],
                }
            ]
        )
    )
    assert "## Ledger recompute" in markdown
    assert "expected cash delta -1.5" in markdown
    assert "| base:0xaa | 2 | 0 | 2 | 2 | yes |" in markdown


def test_ledger_skip_is_visible() -> None:
    markdown = render_markdown(
        report(
            ledger=[
                {
                    "detail": "another writer",
                    "skip": "concurrent_writer_suspected",
                    "cash_before": None,
                    "cash_after": None,
                    "tokens": [],
                }
            ]
        )
    )
    assert "**Skipped:** `concurrent_writer_suspected`" in markdown


def test_fuzz_and_auth_sections() -> None:
    markdown = render_markdown(
        report(
            fuzz=[
                {
                    "case_id": "amount_nan",
                    "expected": "4xx + JSON error body",
                    "status": 422,
                    "ok": True,
                    "detail": "HTTP 422",
                },
                {
                    "case_id": "amount_float_overflow",
                    "expected": "4xx + JSON error body",
                    "status": 500,
                    "ok": False,
                    "detail": "HTTP 500",
                },
            ],
            auth=[
                {"case": "missing", "status": 401, "ok": True, "detail": "HTTP 401"},
                {"case": "control", "status": 201, "ok": True, "detail": "same body"},
            ],
        )
    )
    assert "## Fuzz matrix" in markdown
    assert "2 case(s), 1 not as expected." in markdown
    assert (
        "| `amount_float_overflow` | 4xx + JSON error body | 500 | **no** | HTTP 500 |" in markdown
    )
    assert "## Auth negatives" in markdown
    assert "| missing | 401 | yes | HTTP 401 |" in markdown


def test_race_and_killswitch_sections() -> None:
    markdown = render_markdown(
        report(
            race=[
                {
                    "held_quantity": "2",
                    "sold_quantity": "2",
                    "final_quantity": None,
                    "concurrent_sells": 4,
                    "sell_orders_created": 1,
                    "cash_usd": "10",
                }
            ],
            killswitch=[
                {"step": "observed", "previous_active": False, "previous_reason": None},
                {
                    "step": "blocked",
                    "action": "hold",
                    "reason": "KILL_SWITCH_ACTIVE",
                    "orders_created": 0,
                },
                {"step": "manual_order", "status_code": 201, "status": "filled", "reason": None},
                {"step": "restored", "previous_active": False, "restored_active": False},
            ],
        )
    )
    assert "## Oversell race" in markdown
    assert "sold: 2" in markdown
    assert "## Kill switch" in markdown
    assert "action=hold reason=KILL_SWITCH_ACTIVE orders_created=0" in markdown
    assert "| restored | previous active=False" in markdown


def test_a_report_without_sections_has_no_empty_tables() -> None:
    markdown = render_markdown(report())
    for heading in ("## Chain matrix", "## Fuzz matrix", "## Auth negatives", "## Oversell race"):
        assert heading not in markdown
