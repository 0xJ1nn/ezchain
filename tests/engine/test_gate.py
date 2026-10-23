"""The paper gate reports every check; it never promotes by itself."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ezchain_core import GateEvidence, GateThresholds, evaluate_gate


def test_gate_passes_only_when_every_check_passes() -> None:
    now = datetime(2026, 10, 23, tzinfo=UTC)
    evidence = GateEvidence(
        started_at=now - timedelta(days=15),
        now=now,
        intents=100,
        fills=20,
        backtests=1,
        reconciliation_ok=True,
    )
    result = evaluate_gate(evidence)
    assert result.eligible is True
    assert result.label == "PAPER"
    assert all(check.passed for check in result.checks)


def test_one_failed_check_blocks_promotion_and_is_named() -> None:
    now = datetime(2026, 10, 23, tzinfo=UTC)
    result = evaluate_gate(
        GateEvidence(
            started_at=now - timedelta(days=2),
            now=now,
            intents=5,
            fills=0,
            backtests=0,
            reconciliation_ok=False,
            invariant_breach=True,
        ),
        GateThresholds(min_days=14),
    )
    assert result.eligible is False
    failed = {check.id for check in result.checks if not check.passed}
    assert failed == {"paper_days", "intents", "fills", "backtest", "reconciliation", "invariants"}


def test_thresholds_are_serialized_for_the_api() -> None:
    now = datetime(2026, 10, 23, tzinfo=UTC)
    body = evaluate_gate(
        GateEvidence(
            started_at=now,
            now=now,
            intents=0,
            fills=0,
            backtests=0,
            reconciliation_ok=True,
        ),
        GateThresholds(min_days=7, min_intents=1, min_fills=1, require_backtest=False),
    ).to_dict()
    assert body["thresholds"] == {
        "min_days": 7,
        "min_intents": 1,
        "min_fills": 1,
        "require_backtest": False,
        "require_reconciliation": True,
    }
    assert body["evidence"]["days"] == 0
