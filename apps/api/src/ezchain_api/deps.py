"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from ezchain_adapters import DexScreenerClient, JupiterClient

from .db import session_factory


def get_session() -> Iterator[Session]:
    with session_factory()() as session:
        yield session


def get_dexscreener(request: Request) -> DexScreenerClient:
    return request.app.state.dexscreener  # type: ignore[no-any-return]


def get_jupiter(request: Request) -> JupiterClient:
    return request.app.state.jupiter  # type: ignore[no-any-return]
