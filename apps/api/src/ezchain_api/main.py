"""ezCHAIN API entrypoint."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ezchain_adapters import (
    CetusClient,
    DecimalsResolver,
    DexScreenerClient,
    GeckoTerminalClient,
    HttpQuoteProvider,
    JsonApiClient,
    JupiterClient,
    KyberSwapClient,
    LifiClient,
    OsmosisSqsClient,
    QuoteRouter,
    StonfiClient,
    VeloraClient,
)
from ezchain_engine import KillSwitchExecutor, StrategyScheduler

from .auth import BearerAuthMiddleware
from .config import Settings, get_settings
from .db import create_schema, session_factory
from .observability import ObservabilityMiddleware, configure_logging
from .routers import account, markets, paper, strategies, system
from .services.executor import PaperOrderExecutor
from .services.market_monitor import MarketMonitor
from .services.paper import ensure_genesis
from .services.system_flags import read_kill_switch, seed_kill_switch


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()
    if settings.auto_create_schema:
        create_schema()
    with session_factory()() as session:
        ensure_genesis(session, settings.paper_starting_cash_usd)
        seed_kill_switch(session, settings)

    dexscreener = DexScreenerClient()
    gecko = GeckoTerminalClient()
    decimals = DecimalsResolver(gecko)
    jupiter = JupiterClient()
    # One instance per provider: each owns its own bucket, cache and circuit breaker, so a
    # provider that starts failing cannot slow the ones that still answer.
    providers: list[HttpQuoteProvider] = [
        KyberSwapClient(decimals=decimals),
        VeloraClient(decimals=decimals),
        LifiClient(decimals=decimals),
        CetusClient(decimals=decimals),
        StonfiClient(decimals=decimals),
        OsmosisSqsClient(decimals=decimals),
        jupiter,
    ]
    clients: list[JsonApiClient] = [dexscreener, gecko, *providers]
    for client in clients:
        await client.__aenter__()
    app.state.dexscreener = dexscreener
    # Candle history uses the same GeckoTerminal client as decimals resolution.
    app.state.geckoterminal = gecko
    app.state.quote_router = QuoteRouter(providers=providers, dexscreener=dexscreener)

    def kill_switch_active() -> bool:
        """Read the persisted flag per order so arming applies across processes."""
        with session_factory()() as session:
            return read_kill_switch(session, settings).active

    executor = KillSwitchExecutor(
        PaperOrderExecutor(session_factory(), settings, dexscreener, app.state.quote_router),
        kill_switch_active,
    )
    app.state.order_executor = executor

    scheduler: StrategyScheduler | None = None
    scheduler_task: asyncio.Task[None] | None = None
    if settings.engine_inline:
        scheduler = StrategyScheduler(
            session_factory(),
            dexscreener,
            executor,
            interval_seconds=settings.engine_interval_seconds,
        )
        scheduler_task = asyncio.create_task(scheduler.run_forever())

    monitor: MarketMonitor | None = None
    monitor_task: asyncio.Task[None] | None = None
    if settings.market_scan_inline:
        monitor = MarketMonitor(session_factory(), dexscreener, settings)
        monitor_task = asyncio.create_task(monitor.run_forever())
    app.state.market_monitor = monitor

    try:
        yield
    finally:
        if monitor is not None:
            monitor.stop()
        if monitor_task is not None:
            try:
                await asyncio.wait_for(monitor_task, timeout=5)
            except TimeoutError:
                monitor_task.cancel()
                with suppress(asyncio.CancelledError):
                    await monitor_task
        if scheduler is not None:
            scheduler.stop()
        if scheduler_task is not None:
            await scheduler_task
        for client in clients:
            await client.__aexit__(None, None, None)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="ezCHAIN API",
        version="0.1.0",
        description="Live market browsing and paper execution. No keys, no live money.",
        lifespan=lifespan,
    )
    # Later additions are outermost: access logs wrap CORS, and CORS wraps auth so a
    # 401 still carries CORS headers for the browser.
    app.add_middleware(BearerAuthMiddleware, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    app.add_middleware(ObservabilityMiddleware)
    app.include_router(system.router)
    app.include_router(markets.router)
    app.include_router(paper.router)
    app.include_router(account.router)
    app.include_router(strategies.router)
    return app


app = create_app()
