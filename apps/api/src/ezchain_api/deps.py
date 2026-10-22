"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from ezchain_adapters import DexScreenerClient, QuoteRouter

from .db import session_factory
from .services.market_monitor import MarketMonitor


def get_session() -> Iterator[Session]:
    with session_factory()() as session:
        yield session


def get_dexscreener(request: Request) -> DexScreenerClient:
    return request.app.state.dexscreener  # type: ignore[no-any-return]


def get_quote_router(request: Request) -> QuoteRouter:
    """The per-chain quote ladder, built once at startup with every provider open."""
    return request.app.state.quote_router  # type: ignore[no-any-return]


def get_market_monitor(request: Request) -> MarketMonitor | None:
    """The inline monitor, or ``None`` when observation is disabled for this process."""
    return getattr(request.app.state, "market_monitor", None)
