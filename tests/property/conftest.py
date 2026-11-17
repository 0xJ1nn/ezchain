"""In-process paper fixtures shared by the property, fuzz and load tests.

The database is in-memory SQLite, so no deployment data is touched. The helpers come from
the paper suite's fixtures; only the wiring is repeated here to keep this lane independent.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ezchain_api.models import Base
from tests.paper.conftest import (
    FakeDexScreener,
    FakeJupiter,
    make_settings,
    make_snapshot,
)


@pytest.fixture
def settings():
    return make_settings()


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
def dex() -> FakeDexScreener:
    return FakeDexScreener(make_snapshot(price=Decimal("2000")))


@pytest.fixture
def jupiter() -> FakeJupiter:
    return FakeJupiter(price=Decimal("2000"))
