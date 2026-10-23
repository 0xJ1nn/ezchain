"""Desktop deployment mode: embedded SQLite schema and readiness."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy import text

from ezchain_api.config import Settings, get_settings
from ezchain_api.db import _engine_for
from ezchain_api.routers import system


def test_desktop_settings_skip_production_gate() -> None:
    settings = Settings(
        environment="desktop",
        database_url="sqlite+pysqlite:///:memory:",
        auto_create_schema=True,
        api_token=None,
    )
    assert settings.environment == "desktop"
    assert settings.auto_create_schema is True


def test_desktop_readyz_reports_embedded_schema(
    client_for: Callable[..., TestClient],
) -> None:
    desktop = Settings(
        environment="desktop",
        data_mode="live",
        database_url="sqlite+pysqlite:///:memory:",
        auto_create_schema=True,
    )
    client = client_for(system.router, {get_settings: lambda: desktop})
    response = client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["migration"] == "embedded"


def test_sqlite_engine_uses_wal_and_busy_timeout(tmp_path) -> None:
    database = tmp_path / "desktop.db"
    engine = _engine_for(f"sqlite+pysqlite:///{database}")
    try:
        with engine.connect() as connection:
            assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
            assert connection.execute(text("PRAGMA busy_timeout")).scalar() == 30000
            assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
    finally:
        engine.dispose()
