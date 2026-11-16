"""The execution mode is server-derived and live requires a signed gate record."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ezchain_api.config import Settings, get_settings
from ezchain_api.deps import get_session
from ezchain_api.mode import (
    LIVE_EXECUTOR_SHIPPED,
    LiveGateRefused,
    build_fingerprint,
    derive_execution_mode,
    mode_status,
    verify_live_gate,
)
from ezchain_api.routers import agent, system
from ezchain_core import (
    CUSTODY_HARDWARE,
    KeyHandle,
    PairInfo,
    PositionState,
    RiskLimits,
    SignedTransaction,
    SourceRef,
    TokenSnapshot,
    to_minor_units,
    utcnow,
)
from ezchain_core.reason_codes import GATE_NOT_MET, reason
from ezchain_executor import ExecutionPipeline, FakeVenue, MemoryJournal, SigningBoundary

BUILD = "9f2c0a1"
KEY = "k" * 32
CHAIN = "solana"
TOKEN = "Mint1111111111111111111111111111111111111111"
OWNER = "user:alice"
TEST_ONLY_SIGNATURE = b"ezchain-test-only-signature-vector-0001"


def paper_settings(path: Path | None = None, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "build_hash": BUILD,
        "live_gate_record": None if path is None else str(path),
        "live_gate_key": KEY,
    }
    values.update(overrides)
    return Settings(**values)


def sign(payload: dict[str, Any], key: str = KEY) -> str:
    """The test signs independently of the API so the wire format is the contract."""
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hmac.new(key.encode(), body, hashlib.sha256).hexdigest()


def write_record(path: Path, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 2,
        "build_hash": BUILD,
        "build_fingerprint": build_fingerprint(BUILD),
        "checklist_version": "2026-11-10",
        "approver": "owner",
        "approved_at": "2026-11-10T00:00:00+00:00",
    }
    payload.update(overrides)
    record = {**payload, "signature": sign(payload)}
    path.write_text(json.dumps(record))
    return record


def live(settings: Settings) -> Settings:
    """Bypass boot validation so the derivation itself can be exercised."""
    return settings.model_copy(update={"execution_mode": "live"})


def test_paper_mode_needs_no_gate_record(tmp_path: Path) -> None:
    state = derive_execution_mode(paper_settings())

    assert state.mode == "paper"
    assert state.live_available is False
    assert state.gate is None
    assert mode_status(paper_settings()) == {
        "execution": "paper",
        "live_available": False,
        "live_blocked_reason": "EZCHAIN_EXECUTION_MODE is paper",
    }


def test_live_refusal_names_the_unconfigured_record() -> None:
    with pytest.raises(LiveGateRefused, match="EZCHAIN_LIVE_GATE_RECORD"):
        derive_execution_mode(live(paper_settings()))


def test_live_refusal_names_the_missing_record_path(tmp_path: Path) -> None:
    missing = tmp_path / "live-gate.json"

    with pytest.raises(LiveGateRefused, match=str(missing)):
        derive_execution_mode(live(paper_settings(missing)))


def test_live_refusal_names_an_unreadable_record(tmp_path: Path) -> None:
    record = tmp_path / "live-gate.json"
    record.write_text("{not json")

    with pytest.raises(LiveGateRefused) as raised:
        verify_live_gate(live(paper_settings(record)))

    assert str(record) in str(raised.value)
    assert "not valid JSON" in str(raised.value)


def test_a_signed_gate_record_for_this_build_verifies(tmp_path: Path) -> None:
    record = tmp_path / "live-gate.json"
    write_record(record)

    gate = verify_live_gate(live(paper_settings(record)))

    assert gate.build_hash == BUILD
    assert gate.checklist_version == "2026-11-10"
    assert gate.approver == "owner"
    assert gate.fingerprint == build_fingerprint(BUILD)


def test_a_gate_record_for_another_build_is_refused(tmp_path: Path) -> None:
    record = tmp_path / "live-gate.json"
    write_record(record, build_hash="different-build")

    with pytest.raises(LiveGateRefused, match="different-build"):
        verify_live_gate(live(paper_settings(record)))


def test_a_gate_record_bound_to_another_build_fingerprint_is_refused(tmp_path: Path) -> None:
    record = tmp_path / "live-gate.json"
    write_record(record, build_fingerprint="0" * 64)

    with pytest.raises(LiveGateRefused, match="build_fingerprint"):
        verify_live_gate(live(paper_settings(record)))


def test_a_non_ascii_signature_is_refused_not_a_crash(tmp_path: Path) -> None:
    record = tmp_path / "live-gate.json"
    signed = write_record(record)
    signed["signature"] = "签名-not-hex"
    record.write_text(json.dumps(signed, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(LiveGateRefused, match="not signed"):
        verify_live_gate(live(paper_settings(record)))


def test_an_unknown_record_field_is_refused(tmp_path: Path) -> None:
    record = tmp_path / "live-gate.json"
    write_record(record, not_a_gate_field="surprise")

    with pytest.raises(LiveGateRefused, match="unknown fields: not_a_gate_field"):
        verify_live_gate(live(paper_settings(record)))


def test_an_unset_gate_key_fails_closed(tmp_path: Path) -> None:
    record = tmp_path / "live-gate.json"
    write_record(record)

    with pytest.raises(LiveGateRefused, match="EZCHAIN_LIVE_GATE_KEY is not set"):
        verify_live_gate(live(paper_settings(record, live_gate_key=None)))


def test_a_short_gate_key_fails_closed(tmp_path: Path) -> None:
    record = tmp_path / "live-gate.json"
    write_record(record)

    with pytest.raises(LiveGateRefused, match="at least 32 bytes"):
        verify_live_gate(live(paper_settings(record, live_gate_key="short")))


def test_mode_status_reports_paper_without_leaking_the_record_path(tmp_path: Path) -> None:
    missing = tmp_path / "live-gate.json"

    status = mode_status(live(paper_settings(missing)))

    assert status["execution"] == "paper"
    assert status["live_available"] is False
    assert str(missing) not in str(status["live_blocked_reason"])
    assert "live gate record" in str(status["live_blocked_reason"])


def test_healthz_refusal_does_not_leak_the_record_path(tmp_path: Path) -> None:
    missing = tmp_path / "live-gate.json"
    refused = live(paper_settings(missing))
    app = FastAPI()
    app.include_router(system.router)
    app.dependency_overrides[get_settings] = lambda: refused

    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert response.json()["mode"]["execution"] == "paper"
    assert str(missing) not in response.text


def test_a_tampered_gate_record_is_refused(tmp_path: Path) -> None:
    record = tmp_path / "live-gate.json"
    signed = write_record(record)
    signed["approver"] = "someone-else"
    record.write_text(json.dumps(signed))

    with pytest.raises(LiveGateRefused, match="not signed"):
        verify_live_gate(live(paper_settings(record)))


def test_a_gate_record_missing_the_approver_is_refused(tmp_path: Path) -> None:
    record = tmp_path / "live-gate.json"
    signed = write_record(record)
    del signed["approver"]
    record.write_text(json.dumps(signed))

    with pytest.raises(LiveGateRefused, match="approver"):
        verify_live_gate(live(paper_settings(record)))


def test_a_gate_record_without_a_signature_is_refused(tmp_path: Path) -> None:
    record = tmp_path / "live-gate.json"
    signed = write_record(record)
    del signed["signature"]
    record.write_text(json.dumps(signed))

    with pytest.raises(LiveGateRefused, match="signature"):
        verify_live_gate(live(paper_settings(record)))


def test_a_placeholder_build_hash_cannot_use_a_gate(tmp_path: Path) -> None:
    record = tmp_path / "live-gate.json"
    write_record(record, build_hash="dev")

    with pytest.raises(LiveGateRefused, match="EZCHAIN_BUILD_HASH"):
        verify_live_gate(live(paper_settings(record, build_hash="dev")))


def test_boot_refuses_live_and_names_the_gate_record() -> None:
    with pytest.raises(ValidationError) as raised:
        Settings(environment="test", execution_mode="live")

    assert "EZCHAIN_LIVE_GATE_RECORD" in str(raised.value)


def test_boot_verifies_a_signed_gate_before_refusing_the_unshipped_executor(
    tmp_path: Path,
) -> None:
    record = tmp_path / "live-gate.json"
    write_record(record)

    with pytest.raises(ValidationError) as raised:
        Settings(
            environment="test",
            execution_mode="live",
            build_hash=BUILD,
            live_gate_record=str(record),
            live_gate_key=KEY,
        )

    message = str(raised.value)
    assert "live executor is not shipped" in message
    assert "gate record" not in message


def test_capabilities_ignores_a_client_supplied_mode(client_for, settings: Settings) -> None:
    client = client_for(agent.router)

    response = client.request(
        "GET",
        "/api/v1/agent/capabilities?mode=live&execution_mode=live",
        json={"mode": "live", "execution_mode": "live"},
        headers={"X-Ezchain-Execution-Mode": "live", "X-Ezchain-Mode": "live"},
    )

    assert response.status_code == 200
    assert response.json()["mode"]["execution"] == "paper"
    assert response.json()["mode"]["execution"] == mode_status(settings)["execution"]


def test_healthz_reports_the_same_derived_mode(settings: Settings) -> None:
    app = FastAPI()
    app.include_router(system.router)
    app.include_router(agent.router)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    health = client.get("/healthz")
    capabilities = client.get("/api/v1/agent/capabilities")

    reported = {key: value for key, value in capabilities.json()["mode"].items() if key != "data"}

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["mode"] == reported
    assert health.json()["mode"]["execution"] == "paper"


# --- The refusal carries a stable code -----------------------------------------


def test_a_live_start_without_a_gate_record_is_denied_with_the_stable_code() -> None:
    """The refusal is a code a caller can branch on, not only a sentence."""
    with pytest.raises(LiveGateRefused) as raised:
        derive_execution_mode(live(paper_settings()))

    assert raised.value.code == GATE_NOT_MET
    assert str(raised.value).startswith(GATE_NOT_MET)
    assert reason(GATE_NOT_MET).category == "governance"


def test_the_read_surface_reports_the_code_without_the_record_path(tmp_path: Path) -> None:
    missing = tmp_path / "live-gate.json"

    status = mode_status(live(paper_settings(missing)))

    assert status["reason_code"] == GATE_NOT_MET
    assert str(missing) not in str(status)
    assert status["live_available"] is False


def test_no_client_input_can_set_the_mode_on_any_mode_surface(
    session: Any, settings: Settings
) -> None:
    """Query, body and headers are ignored everywhere the mode reaches a client."""
    app = FastAPI()
    app.include_router(system.router)
    app.include_router(agent.router)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = lambda: session
    client = TestClient(app)
    hostile = {"mode": "live", "execution_mode": "live", "EZCHAIN_EXECUTION_MODE": "live"}
    headers = {"X-Ezchain-Execution-Mode": "live", "X-Ezchain-Mode": "live"}

    for path in ("/healthz", "/api/v1/agent/capabilities", "/api/v1/agent/state"):
        response = client.request("GET", path, params=hostile, json=hostile, headers=headers)

        assert response.status_code == 200, path
        reported = {key: value for key, value in response.json()["mode"].items() if key != "data"}
        assert reported == mode_status(settings), path


# --- Fee and PnL parity at the journal seam -------------------------------------


@dataclass
class _OwnedSigner:
    """A custody-shaped signer for the executor's live-mode pipeline in tests."""

    handle: KeyHandle

    def sign(self, prepared: Any, approved: Any) -> SignedTransaction:
        return SignedTransaction(
            raw=prepared.raw,
            signature=TEST_ONLY_SIGNATURE,
            approved=approved,
            payload_digest=prepared.payload_digest,
        )


def _sell() -> Any:
    from ezchain_core import Intent

    return Intent(
        side="sell",
        amount_usd=Decimal("0"),
        quantity=Decimal("10"),
        reduce_only=True,
        chain=CHAIN,
        token_address=TOKEN,
        idempotency_key="parity-1",
        actor=OWNER,
    )


def _snapshot() -> TokenSnapshot:
    seen = utcnow()
    return TokenSnapshot(
        chain=CHAIN,
        address=TOKEN,
        symbol="TEST",
        name="Test",
        price_usd=Decimal("2"),
        liquidity_usd=Decimal("500000"),
        volume_24h_usd=Decimal("1000000"),
        price_change_m5=Decimal("0"),
        price_change_h1=Decimal("1"),
        price_change_h6=Decimal("0"),
        price_change_h24=Decimal("0"),
        pairs=(
            PairInfo(
                pair_address="pair-1",
                dex="raydium",
                quote_token="USDC",
                price_usd=Decimal("2"),
                liquidity_usd=Decimal("500000"),
                volume_24h_usd=Decimal("1000000"),
                url=None,
            ),
        ),
        source=SourceRef("dexscreener", None, seen),
        pair_created_at=seen - timedelta(days=1),
    )


def _limits() -> RiskLimits:
    return RiskLimits(
        max_order_usd=Decimal("1000"),
        max_position_usd=Decimal("2500"),
        max_open_positions=10,
        max_daily_loss_usd=Decimal("500"),
        max_quote_age_seconds=60,
        min_liquidity_usd=Decimal("10000"),
        paper_mark_max_order_usd=Decimal("250"),
        min_pair_age_minutes=10,
        max_price_impact_pct=Decimal("0.5"),
    )


@pytest.mark.asyncio
async def test_paper_and_live_book_one_receipt_to_the_same_minor_unit() -> None:
    """One fill, one receipt: mode changes no fee or PnL figure at the journal seam.

    The API's live path is not shippable in this build (``LIVE_EXECUTOR_SHIPPED`` is
    false), so this is the journal-from-receipt seam of the shared executor pipeline,
    not an end-to-end live execution. The executor's own live mode is reachable with a
    custody-shaped signer, which is what makes the comparison concrete.
    """
    assert LIVE_EXECUTOR_SHIPPED is False
    handle = KeyHandle(
        handle_id="h-alice-1",
        owner=OWNER,
        custody=CUSTODY_HARDWARE,  # type: ignore[arg-type]
        chain=CHAIN,
        address="Wallet11111111111111111111111111111111111111",
        fingerprint=hashlib.sha256(TEST_ONLY_SIGNATURE).hexdigest(),
    )
    position = PositionState(
        chain=CHAIN,
        token_address=TOKEN,
        symbol="TEST",
        quantity=Decimal("20"),
        cost_usd=Decimal("40"),
    )

    async def run(mode: str):
        venue = FakeVenue(
            mode="partial", partial_ratio=Decimal(1) / Decimal(3), fee_usd=Decimal("0.07")
        )
        signer = _OwnedSigner(handle)
        boundary = SigningBoundary(
            cluster="devnet",
            recipient=handle.address,
            program_id="JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
            adapter=venue,
            signer=signer,  # type: ignore[arg-type]
        )
        journal = MemoryJournal()
        pipe = ExecutionPipeline(
            venue=venue,
            sink=journal,
            limits=_limits(),
            signing=boundary,
            mode=mode,  # type: ignore[arg-type]
            timeout_seconds=0.2,
        )
        result = await pipe.run(_sell(), snapshot=_snapshot(), position=position)
        assert result.status == "partially_filled"
        assert result.fill is not None and result.journal is not None
        return result, journal

    paper, paper_journal = await run("paper")
    live, live_journal = await run("live")

    assert (
        paper.fill.quantity,
        paper.fill.notional_usd,
        paper.fill.fee_usd,
        paper.fill.transaction_digest,
    ) == (
        live.fill.quantity,
        live.fill.notional_usd,
        live.fill.fee_usd,
        live.fill.transaction_digest,
    ), "one receipt must feed both modes"
    assert paper.journal.entries == live.journal.entries
    assert sum(to_minor_units(e.usd_value) for e in paper_journal.entries) == 0

    def legs(entries: tuple[Any, ...], kind: str) -> tuple[int, ...]:
        return tuple(to_minor_units(entry.usd_value) for entry in entries if entry.kind == kind)

    assert legs(paper.journal.entries, "fee") == legs(live.journal.entries, "fee")
    assert legs(paper.journal.entries, "pnl") == legs(live.journal.entries, "pnl")
    assert legs(paper.journal.entries, "pnl"), "a sell must carry a realized PnL leg"
