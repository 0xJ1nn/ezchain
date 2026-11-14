from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ezchain_api.services import pnl


def _line(
    group_id: str, account: str, amount: str, usd: str, at: datetime, seq: int
) -> pnl.LedgerLine:
    return pnl.LedgerLine(
        group_id=group_id,
        account=account,
        amount=Decimal(amount),
        usd_value=Decimal(usd),
        created_at=at,
    )


def test_series_preserves_ledger_order_when_observed_times_are_out_of_order() -> None:
    # Group B is created later in the ledger but carries an earlier observed_at.
    # The series must follow ledger/seq order, not the observed timestamp.
    base = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    token_account = "position:solana:TOK"
    lines = [
        _line("g1", token_account, "10", "10", base, 1),
        _line("g1", pnl.CASH_ACCOUNT, "0", "-10", base, 2),
        _line("g2", token_account, "-10", "-10", base + timedelta(minutes=1), 3),
        _line("g2", pnl.CASH_ACCOUNT, "0", "20", base - timedelta(hours=1), 4),
    ]
    marks = {}
    result = pnl.performance_series(lines, marks=marks, window=None, now=base + timedelta(hours=2))
    # g1 first (seq 1/2), g2 second (seq 3/4) even though g2's observed_at is earlier.
    assert [p["t"] for p in result["realized_pnl_series"]] == [
        base.isoformat(),
        (base - timedelta(hours=1)).isoformat(),
    ]
    # realized values accumulate in ledger order: 0 then 10.
    assert [p["value"] for p in result["realized_pnl_series"]] == ["0", "10"]


def test_empty_series_returns_reason_not_zero() -> None:
    result = pnl.performance_series([], marks={}, window=None)
    assert result["equity_series"] == []
    assert result["realized_pnl_series"] == []
    assert result["drawdown_series"] == []
    assert result["reason"] == "NO_SERIES_DATA"
    assert result["complete"] is True


def test_drawdown_math_on_known_series() -> None:
    base = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    # Two equity points: 100 then 90 -> drawdown -10%.
    lines = [
        _line("g1", pnl.CASH_ACCOUNT, "0", "100", base, 1),
        _line("g2", pnl.CASH_ACCOUNT, "0", "-10", base + timedelta(minutes=1), 2),
    ]
    result = pnl.performance_series(lines, marks={}, window=None)
    assert [p["value"] for p in result["equity_series"]] == ["100", "90"]
    assert [p["value"] for p in result["drawdown_series"]] == ["0", "-10"]


def test_unknown_mark_series_incomplete() -> None:
    base = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    token = ("solana", "TOK")
    lines = [
        _line("g1", "position:" + token[0] + ":" + token[1], "10", "10", base, 1),
        _line("g1", pnl.CASH_ACCOUNT, "0", "-10", base, 2),
    ]
    marks = {token: None}
    result = pnl.performance_series(lines, marks=marks, window=None)
    assert result["marks_unknown"] is True
    assert result["complete"] is False
    assert result["reason"] == "MARKS_UNKNOWN"
    # equity point is null, not zero
    assert result["equity_series"][0]["value"] is None
