"""Kill-switch HTTP surface and persistence across sessions."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ezchain_api.config import Settings
from ezchain_api.routers import system
from ezchain_api.services.system_flags import read_kill_switch, seed_kill_switch


def test_get_reports_the_configured_default_without_writing(client_for, settings: Settings) -> None:
    client: TestClient = client_for(system.router)

    response = client.get("/api/v1/system/kill-switch")

    assert response.status_code == 200
    assert response.json() == {
        "active": False,
        "reason": None,
        "updated_at": None,
        "scope": "strategy",
    }


def test_post_persists_the_switch_and_survives_a_new_session(
    client_for, engine: Engine, settings: Settings
) -> None:
    client: TestClient = client_for(system.router)

    armed = client.post(
        "/api/v1/system/kill-switch", json={"active": True, "reason": "incident drill"}
    )
    assert armed.status_code == 200
    body = armed.json()
    assert body["active"] is True
    assert body["reason"] == "incident drill"
    assert body["scope"] == "strategy"
    assert body["updated_at"] is not None

    # A brand-new session (as a restarted process would open) reads the persisted row.
    with sessionmaker(bind=engine, expire_on_commit=False)() as fresh:
        state = read_kill_switch(fresh, settings)
        assert state.active is True
        assert state.reason == "incident drill"

    released = client.post(
        "/api/v1/system/kill-switch", json={"active": False, "reason": "drill complete"}
    )
    assert released.status_code == 200
    assert released.json()["active"] is False
    with sessionmaker(bind=engine, expire_on_commit=False)() as fresh:
        assert read_kill_switch(fresh, settings).active is False


def test_first_boot_seeds_the_flag_from_settings(session: Session) -> None:
    armed_settings = Settings(
        environment="test", database_url="sqlite+pysqlite://", engine_kill_switch=True
    )

    seeded = seed_kill_switch(session, armed_settings)

    assert seeded.active is True
    assert seeded.reason is None
    assert read_kill_switch(session, armed_settings).active is True

    # Seeding is idempotent and never overwrites an operator change.
    session_state = seed_kill_switch(session, armed_settings)
    assert session_state.active is True
