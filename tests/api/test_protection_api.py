"""Protection HTTP surface: arm, list, update, disarm, idempotency conflict."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ezchain_api.routers import protection as protection_router

CHAIN = "blast"
TOKEN = "0x0000000000000000000000000000000000000abc"


def test_arm_list_update_disarm_round_trip(client_for) -> None:
    client: TestClient = client_for(protection_router.router)

    armed = client.post(
        "/api/v1/protections",
        json={
            "chain": CHAIN,
            "token_address": TOKEN,
            "kind": "stop_loss",
            "params": {"loss_pct": "8"},
            "idempotency_key": "api-arm-1",
        },
    )
    assert armed.status_code == 201, armed.text
    body = armed.json()
    assert body["status"] == "armed"
    assert body["version"] == 1
    assert body["kind"] == "stop_loss"
    protection_id = body["id"]

    listed = client.get("/api/v1/protections", params={"chain": CHAIN, "token_address": TOKEN})
    assert listed.status_code == 200
    assert any(item["id"] == protection_id for item in listed.json())

    updated = client.patch(
        f"/api/v1/protections/{protection_id}",
        json={"params": {"loss_pct": "12"}, "expected_version": 1},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    assert updated.json()["params"]["loss_pct"] == "12"

    disarmed = client.delete(f"/api/v1/protections/{protection_id}")
    assert disarmed.status_code == 200, disarmed.text
    assert disarmed.json()["status"] == "cancelled"


def test_same_key_different_payload_is_conflict(client_for) -> None:
    client: TestClient = client_for(protection_router.router)
    first = client.post(
        "/api/v1/protections",
        json={
            "chain": CHAIN,
            "token_address": TOKEN,
            "kind": "stop_loss",
            "params": {"loss_pct": "8"},
            "idempotency_key": "api-arm-conflict",
        },
    )
    assert first.status_code == 201

    conflict = client.post(
        "/api/v1/protections",
        json={
            "chain": CHAIN,
            "token_address": TOKEN,
            "kind": "stop_loss",
            "params": {"loss_pct": "20"},
            "idempotency_key": "api-arm-conflict",
        },
    )
    assert conflict.status_code == 409, conflict.text

    retry = client.post(
        "/api/v1/protections",
        json={
            "chain": CHAIN,
            "token_address": TOKEN,
            "kind": "stop_loss",
            "params": {"loss_pct": "8"},
            "idempotency_key": "api-arm-conflict",
        },
    )
    assert retry.status_code == 201
    assert retry.json()["id"] == first.json()["id"]


def test_profile_crud_and_resolution(client_for) -> None:
    client: TestClient = client_for(protection_router.router)
    created = client.post(
        "/api/v1/protection-profiles",
        json={
            "name": "Global defaults",
            "profile_type": "manual",
            "chain": None,
            "defaults": {"stop_loss": {"loss_pct": "8"}},
        },
    )
    assert created.status_code == 201, created.text

    profiles = client.get("/api/v1/protection-profiles")
    assert profiles.status_code == 200
    assert any(item["name"] == "Global defaults" for item in profiles.json())
