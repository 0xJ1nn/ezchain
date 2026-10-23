"""Preflight safety: non-paper mode is refused before any mutation."""

from __future__ import annotations

from typing import Any

import pytest
from tools.stress.scenarios import PreflightError, preflight


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class FakeHarness:
    def __init__(self, account_mode: object, *, state_readable: bool = True) -> None:
        self.account_mode = account_mode
        self.state_readable = state_readable
        self.requests: list[str] = []
        self.invariants: list[str] = []
        self.skipped: list[str] = []
        self.findings: list[Any] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        scenario: str,
        params: object = None,
    ) -> FakeResponse:
        self.requests.append(path)
        return FakeResponse(200)

    def json_object(
        self, response: FakeResponse | None, *, scenario: str, label: str
    ) -> dict[str, object]:
        if label == "health":
            return {"status": "ok", "db": "ok"}
        return {
            "cash_usd": "10000",
            "equity_usd": "10000",
            "realized_usd": "0",
            "unrealized_usd": "0",
            "complete": True,
            "marks_unknown": False,
            "known_market_value_usd": "0",
            "unpriced_positions": [],
            "positions": [],
            "by_asset": [
                {
                    "chain": "settlement",
                    "token_address": None,
                    "symbol": "USD",
                    "kind": "cash",
                    "quantity": "10000",
                    "mark_usd": "1",
                    "market_value_usd": "10000",
                    "share_pct": "100.00",
                    "source": None,
                    "mark_status": "known",
                }
            ],
            "by_chain": [
                {
                    "chain": "settlement",
                    "assets": 1,
                    "unpriced_assets": 0,
                    "market_value_usd": "10000",
                    "share_pct": "100.00",
                    "mark_status": "known",
                }
            ],
            "mode": self.account_mode,
        }

    def json_list(
        self, response: FakeResponse | None, *, scenario: str, label: str
    ) -> list[dict[str, object]] | None:
        if not self.state_readable:
            return None
        return []

    def register_invariants(self, *identifiers: str) -> None:
        self.invariants.extend(identifiers)

    def skip_invariant(self, identifier: str) -> None:
        self.skipped.append(identifier)

    def add_findings(self, findings: object) -> None:
        self.findings.append(findings)


def test_refuses_live_mode_before_mutating() -> None:
    harness = FakeHarness("live")
    with pytest.raises(PreflightError, match="REFUSING_NON_PAPER"):
        preflight(harness)  # type: ignore[arg-type]
    assert harness.requests == ["/health", "/api/v1/paper/account"]
    assert harness.invariants == []


def test_refuses_missing_mode() -> None:
    with pytest.raises(PreflightError, match="REFUSING_NON_PAPER"):
        preflight(FakeHarness(None))  # type: ignore[arg-type]


def test_accepts_exact_paper_mode() -> None:
    harness = FakeHarness("paper")
    preflight(harness)  # type: ignore[arg-type]
    assert harness.invariants == [
        "preflight.health",
        "preflight.paper_mode",
        "preflight.state_readable",
    ]
    assert harness.requests == [
        "/health",
        "/api/v1/paper/account",
        "/api/v1/paper/positions",
        "/api/v1/paper/fills",
    ]
    assert harness.skipped == []


def test_refuses_an_unreadable_paper_state() -> None:
    """A corrupted account (e.g. a negative position) must fail closed, not cascade."""
    harness = FakeHarness("paper", state_readable=False)
    with pytest.raises(PreflightError, match="PREFLIGHT_FAILED"):
        preflight(harness)  # type: ignore[arg-type]
    assert harness.skipped == ["preflight.state_readable"]
