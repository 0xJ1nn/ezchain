"""Kill-switch HTTP surface and persistence across sessions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ezchain_api.config import Settings
from ezchain_api.db import Base
from ezchain_api.models import SystemFlagRow
from ezchain_api.routers import system
from ezchain_api.services import system_flags
from ezchain_api.services.system_flags import (
    KILL_SWITCH_KEY,
    read_kill_switch,
    seed_kill_switch,
)
from ezchain_core import utcnow


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


def test_release_requires_a_reason(client_for) -> None:
    client: TestClient = client_for(system.router)

    rejected = client.post("/api/v1/system/kill-switch", json={"active": False})
    assert rejected.status_code == 422

    blank = client.post("/api/v1/system/kill-switch", json={"active": False, "reason": "   "})
    assert blank.status_code == 422

    # Arming still works without a reason; releasing with one works.
    assert client.post("/api/v1/system/kill-switch", json={"active": True}).status_code == 200
    assert (
        client.post(
            "/api/v1/system/kill-switch",
            json={"active": False, "reason": "drill complete"},
        ).status_code
        == 200
    )


def test_seed_recovers_when_another_process_wins_the_insert(
    tmp_path: Path, monkeypatch: Any
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'seed-race.db'}"
    eng = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _wal(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.execute("PRAGMA journal_mode=WAL")

    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, expire_on_commit=False)
    settings = Settings(environment="test", database_url=url, engine_kill_switch=False)

    def racing_set(target: Session, *, active: bool, reason: str | None) -> system_flags.KillSwitch:
        # A second process commits the row, then our insert hits the primary key.
        with factory() as winner:
            winner.add(
                SystemFlagRow(
                    key=KILL_SWITCH_KEY,
                    value={"active": True, "reason": "winner"},
                    updated_at=utcnow(),
                )
            )
            winner.commit()
        target.add(
            SystemFlagRow(
                key=KILL_SWITCH_KEY,
                value={"active": False, "reason": "loser"},
                updated_at=utcnow(),
            )
        )
        target.commit()  # unique violation
        raise AssertionError("expected the duplicate insert to fail")

    try:
        with factory() as session:
            assert session.get(SystemFlagRow, KILL_SWITCH_KEY) is None
            monkeypatch.setattr(system_flags, "set_kill_switch", racing_set)

            state = seed_kill_switch(session, settings)

        assert state.active is True
        assert state.reason == "winner"
        with factory() as fresh:
            assert read_kill_switch(fresh, settings).reason == "winner"
    finally:
        eng.dispose()
