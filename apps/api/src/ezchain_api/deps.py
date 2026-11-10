"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import cast

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from ezchain_adapters import DexScreenerClient, GeckoTerminalClient, QuoteRouter
from ezchain_engine import PluginHost, PluginRegistry

from .config import Settings, get_settings
from .db import session_factory
from .services.market_monitor import MarketMonitor
from .services.provider_secrets import (
    SecretCipher,
    build_secret_cipher,
    forget_stored_secret,
)


def get_session() -> Iterator[Session]:
    with session_factory()() as session:
        yield session


def get_secret_cipher(settings: Settings = Depends(get_settings)) -> SecretCipher:
    """The configured provider-key store. Availability is checked on creation."""
    return build_secret_cipher(settings)


def get_secret_forgetter(
    settings: Settings = Depends(get_settings),
) -> Callable[[str], None]:
    """Delete a stored key through the backend named by its prefix."""

    def forget(stored: str) -> None:
        forget_stored_secret(stored, settings)

    return forget


def get_dexscreener(request: Request) -> DexScreenerClient:
    return request.app.state.dexscreener  # type: ignore[no-any-return]


def get_geckoterminal(request: Request) -> GeckoTerminalClient:
    return request.app.state.geckoterminal  # type: ignore[no-any-return]


def get_quote_router(request: Request) -> QuoteRouter:
    """The per-chain quote ladder, built once at startup with every provider open."""
    return request.app.state.quote_router  # type: ignore[no-any-return]


def get_market_monitor(request: Request) -> MarketMonitor | None:
    """The inline monitor, or ``None`` when observation is disabled for this process."""
    return getattr(request.app.state, "market_monitor", None)


_EMPTY_REGISTRY = PluginRegistry(include_entry_points=False)


def get_plugin_registry(request: Request) -> PluginRegistry:
    """The process catalogue, discovered once at startup and reloadable by an operator.

    Tests and embedders that do not start the production lifespan get an empty catalogue
    instead of an AttributeError. Plugin strategies then fail closed with PLUGIN_NOT_FOUND.
    """
    return cast(
        PluginRegistry,
        getattr(request.app.state, "plugin_registry", _EMPTY_REGISTRY),
    )


_EMPTY_HOST = PluginHost()


def get_plugin_host(request: Request) -> PluginHost:
    """The supervised worker pool. Absent means plugin strategies fail closed."""
    return cast(PluginHost, getattr(request.app.state, "plugin_host", _EMPTY_HOST))
