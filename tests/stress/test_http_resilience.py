"""Transport resilience: one retry for an idempotent read, never for a mutation."""

from __future__ import annotations

import httpx
import pytest
from tools.stress.config import RunConfig
from tools.stress.http import Harness


class StubResponse:
    status_code = 200
    text = "{}"

    def json(self) -> dict[str, object]:
        return {}


class FlakyClient:
    """Raises a transport error ``failures`` times, then answers 200."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0
        self.closed = False

    def request(self, *args: object, **kwargs: object) -> StubResponse:
        self.calls += 1
        if self.calls <= self.failures:
            raise httpx.ReadError("[Errno 104] Connection reset by peer")
        return StubResponse()

    def close(self) -> None:
        self.closed = True


def _harness(monkeypatch: pytest.MonkeyPatch) -> Harness:
    monkeypatch.delenv("EZCHAIN_STRESS_API_TOKEN", raising=False)
    return Harness(RunConfig(base_url="http://127.0.0.1:8000"))


def test_get_retries_once_on_a_reset_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _harness(monkeypatch)
    clients = [FlakyClient(failures=99), FlakyClient(failures=0)]
    monkeypatch.setattr(harness, "_client", lambda origin, auth="default": clients.pop(0))
    try:
        response = harness.request("GET", "/health", scenario="test")
        assert response is not None
        assert response.status_code == 200
        assert harness.transport_retries == 1
        assert harness.findings == []
        assert "http.no_connection_errors" not in {f.invariant for f in harness.findings}
    finally:
        harness.close_thread_clients()


def test_get_still_reports_a_persistent_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _harness(monkeypatch)
    monkeypatch.setattr(harness, "_client", lambda origin, auth="default": FlakyClient(99))
    try:
        assert harness.request("GET", "/health", scenario="test") is None
        assert harness.transport_retries == 1
        assert [f.code for f in harness.findings] == ["CONNECTION_ERROR"]
    finally:
        harness.close_thread_clients()


def test_mutations_are_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A retried POST could duplicate a side effect, so it is attempted exactly once."""
    harness = _harness(monkeypatch)
    client = FlakyClient(failures=99)
    monkeypatch.setattr(harness, "_client", lambda origin, auth="default": client)
    try:
        assert (
            harness.request(
                "POST",
                "/api/v1/paper/orders",
                scenario="test",
                json_body={"chain": "solana"},
            )
            is None
        )
        assert client.calls == 1
        assert harness.transport_retries == 0
    finally:
        harness.close_thread_clients()


def test_matrix_has_its_own_order_allowance(monkeypatch: pytest.MonkeyPatch) -> None:
    """The matrix runs last in ``--mode all`` and must not be starved by the load cap."""
    harness = _harness(monkeypatch)
    try:
        for _ in range(harness.max_orders):
            assert harness.reserve_order_slot() is True
        assert harness.reserve_order_slot() is False
        assert harness.reserve_matrix_slot() is True
        for _ in range(harness.max_matrix_orders - 1):
            assert harness.reserve_matrix_slot() is True
        assert harness.reserve_matrix_slot() is False
    finally:
        harness.close_thread_clients()


def test_a_broken_paper_state_keeps_the_first_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _harness(monkeypatch)
    try:
        assert harness.paper_state_broken is None
        harness.mark_paper_state_broken("oversold")
        harness.mark_paper_state_broken("something else")
        assert harness.paper_state_broken == "oversold"
    finally:
        harness.close_thread_clients()


def test_skips_carry_their_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _harness(monkeypatch)
    try:
        harness.register_invariants("ledger.positions_match_fills")
        harness.skip_invariant("ledger.positions_match_fills", "concurrent_writer_suspected")
        rows = {row["id"]: row for row in harness.invariant_report()}
        assert rows["ledger.positions_match_fills"]["status"] == "skip"
        assert rows["ledger.positions_match_fills"]["detail"] == "concurrent_writer_suspected"
        harness.unskip_invariant("ledger.positions_match_fills")
        rows = {row["id"]: row for row in harness.invariant_report()}
        assert rows["ledger.positions_match_fills"]["status"] == "pass"
    finally:
        harness.close_thread_clients()
