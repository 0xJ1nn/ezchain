"""The execution mode is server-derived and live requires a signed gate record."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ezchain_api.config import Settings, get_settings
from ezchain_api.mode import (
    LiveGateRefused,
    build_fingerprint,
    derive_execution_mode,
    mode_status,
    verify_live_gate,
)
from ezchain_api.routers import agent, system

BUILD = "9f2c0a1"
KEY = "k" * 32


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
