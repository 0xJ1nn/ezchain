"""Shared fixtures for the strategy engine tests.

SQLite keeps the tests fast and deterministic. The production schema is Postgres; the
models use a JSON variant so the same metadata works on both.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import ezchain_api.models  # noqa: F401  (register tables on Base.metadata)
from ezchain_api.db import Base
from ezchain_api.models import StrategyRow


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    yield factory
    engine.dispose()


@pytest.fixture
def add_strategy(session_factory: sessionmaker[Session]) -> Callable[..., str]:
    def _add(
        *,
        kind: str = "momentum",
        params: dict[str, Any] | None = None,
        status: str = "active",
        last_run_at: datetime | None = None,
        chain: str = "solana",
        token_address: str = "So11111111111111111111111111111111111111112",
        token_symbol: str = "TEST",
    ) -> str:
        with session_factory() as session:
            row = StrategyRow(
                name=f"test {kind}",
                chain=chain,
                token_address=token_address,
                token_symbol=token_symbol,
                kind=kind,
                params=params or {},
                status=status,
                last_run_at=last_run_at,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.id

    return _add
