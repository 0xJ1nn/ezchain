"""Shared fixtures for the model-provider API tests.

The tests exercise the router against an in-memory database and a scripted model transport.
No network, no keyring, no real key and no provider account is involved.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import ezchain_api.models  # noqa: F401  (register tables on Base.metadata)
from ezchain_api.config import Settings, get_settings
from ezchain_api.db import Base
from ezchain_api.deps import get_secret_cipher, get_secret_forgetter, get_session


class MemoryCipher:
    """A deterministic store double. It keeps the secret out of the database row."""

    name = "memory"

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def ensure_available(self) -> None:
        return None

    def encrypt(self, secret: str) -> str:
        stored = f"memory:v1:{len(self.values)}"
        self.values[stored] = secret
        return stored

    def decrypt(self, stored: str) -> str:
        return self.values[stored]

    def forget(self, stored: str) -> None:
        self.values.pop(stored, None)


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
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        yield db


@pytest.fixture
def cipher() -> MemoryCipher:
    return MemoryCipher()


@pytest.fixture
def client_for(
    session: Session, settings: Settings, cipher: MemoryCipher
) -> Callable[..., TestClient]:
    def _make(router: APIRouter, overrides: dict[Any, Any] | None = None) -> TestClient:
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_secret_cipher] = lambda: cipher
        app.dependency_overrides[get_secret_forgetter] = lambda: cipher.forget
        if overrides:
            app.dependency_overrides.update(overrides)
        return TestClient(app)

    return _make
