"""Shared fixtures for API-edge tests.

These tests exercise routers directly against a SQLite database. The real lifespan is
not started: composition (network clients, scheduler) is out of scope here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import ezchain_api.models  # noqa: F401  (register tables on Base.metadata)
from ezchain_api.config import Settings, get_settings
from ezchain_api.db import Base
from ezchain_api.deps import get_session


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        data_mode="fixture",
        database_url="sqlite+pysqlite://",
        auto_create_schema=False,
    )


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        # SQLite ignores foreign keys unless asked; Postgres enforces them.
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        yield db


@pytest.fixture
def client_for(session: Session, settings: Settings) -> Callable[..., TestClient]:
    def _make(router: APIRouter, overrides: dict[Any, Any] | None = None) -> TestClient:
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[get_settings] = lambda: settings
        if overrides:
            app.dependency_overrides.update(overrides)
        return TestClient(app)

    return _make
