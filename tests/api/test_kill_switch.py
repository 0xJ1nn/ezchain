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
from ezchain_api.schemas import KillSwitchOut
from ezchain_api.services import system_flags
from ezchain_api.services.system_flags import (
    KILL_SWITCH_KEY,
    read_kill_switch,
    seed_kill_switch,
    set_kill_switch,
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
        "scope": "global",
        "scopes": ["global", "strategy"],
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
    assert body["scope"] == "global"
    assert body["scopes"] == ["global", "strategy"]
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


def test_arm_and_release_are_symmetric_single_step_actions(client_for) -> None:
    """The switch is one click in either direction; the reason is optional bookkeeping.

    Arming is the risk-reducing direction and releasing is the owner getting their own
    trading back, so a mandatory reason on release alone gated the wrong way. A supplied
    reason is still stored in either direction.
    """
    client: TestClient = client_for(system.router)

    armed = client.post("/api/v1/system/kill-switch", json={"active": True})
    assert armed.status_code == 200
    assert armed.json()["reason"] is None

    released = client.post("/api/v1/system/kill-switch", json={"active": False})
    assert released.status_code == 200
    assert released.json()["active"] is False
    assert released.json()["reason"] is None

    # A reason is optional, not forbidden: it is accepted and stored in both directions.
    armed = client.post(
        "/api/v1/system/kill-switch", json={"active": True, "reason": "incident drill"}
    )
    assert armed.status_code == 200
    assert armed.json()["reason"] == "incident drill"
    assert client.get("/api/v1/system/kill-switch").json()["reason"] == "incident drill"

    released = client.post(
        "/api/v1/system/kill-switch", json={"active": False, "reason": "drill complete"}
    )
    assert released.status_code == 200
    assert released.json()["reason"] == "drill complete"


def test_an_unknown_field_is_refused_and_leaves_the_switch_untouched(client_for) -> None:
    """A misspelt switch field is an error, not a silent drop that arms nothing."""
    client: TestClient = client_for(system.router)

    response = client.post(
        "/api/v1/system/kill-switch",
        json={"active": True, "reason": "drill", "scope": "global"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert [error["loc"] for error in detail] == [["body", "scope"]]
    assert detail[0]["type"] == "extra_forbidden"
    # The refused request is not an operator action: the persisted state is unchanged.
    assert client.get("/api/v1/system/kill-switch").json()["active"] is False


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


def test_schema_scopes_track_the_system_flags_source() -> None:
    """The schema must not keep a second copy of the scopes list."""
    schema = KillSwitchOut(active=True)

    assert list(schema.scopes) == list(system_flags.KILL_SWITCH_SCOPES)


def test_a_read_never_serves_a_released_state_cached_before_arming(tmp_path: Path) -> None:
    """The flag is a row read, not an identity-map value.

    The reader keeps the released row strongly referenced, so the weak identity map
    cannot forget it and a plain ``session.get`` would keep returning it. While another
    process arms the switch, the next read must still issue a SELECT and observe the arm;
    serving the cached instance would keep obeying the released state for the life of the
    session, the stale state the one-tick rule forbids.
    """
    url = f"sqlite+pysqlite:///{tmp_path / 'flag-fresh.db'}"
    reader_engine = create_engine(url)
    writer_engine = create_engine(url)
    Base.metadata.create_all(writer_engine)
    settings = Settings(environment="test", database_url=url, engine_kill_switch=False)
    reader_maker = sessionmaker(bind=reader_engine, expire_on_commit=False)
    writer_maker = sessionmaker(bind=writer_engine, expire_on_commit=False)

    selects: list[str] = []

    @event.listens_for(reader_engine, "before_cursor_execute")
    def _record_flag_select(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: Any
    ) -> None:
        if "system_flags" in statement and statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    try:
        with writer_maker() as writer:
            set_kill_switch(writer, active=False, reason=None)
        with reader_maker() as reader:
            # Hold the released row strongly before reading it: the identity map alone is
            # weak, and a collected row would be re-queried for the wrong reason.
            cached = reader.get(SystemFlagRow, KILL_SWITCH_KEY)
            assert cached is not None and cached.value["active"] is False
            assert read_kill_switch(reader, settings).active is False
            # End the read transaction so the other connection can commit; the row stays
            # in this session's identity map because expire_on_commit is false.
            reader.commit()
            with writer_maker() as writer:
                set_kill_switch(writer, active=True, reason="armed mid-flight")
            before = len(selects)
            assert read_kill_switch(reader, settings).active is True
            assert cached.value["active"] is True  # the cached instance was refreshed
            # The second read crossed the process boundary: exactly one fresh SELECT, not
            # an identity-map hit that would still serve the released state.
            assert len(selects) == before + 1
    finally:
        reader_engine.dispose()
        writer_engine.dispose()
