"""ezCHAIN API entrypoint."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any, cast

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
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
    ProviderBudgetManager,
    ProviderKeyUnavailable,
    QuoteRouter,
    StonfiClient,
    VeloraClient,
)
from ezchain_engine import KillSwitchExecutor, PluginHost, PluginRegistry, StrategyScheduler

from .auth import BearerAuthMiddleware
from .config import Settings, get_settings
from .db import create_schema, session_factory
from .errors import request_validation_error
from .observability import ObservabilityMiddleware, configure_logging
from .rate_limit import ReadRateLimitMiddleware
from .routers import (
    account,
    agent,
    backtests,
    graph,
    markets,
    paper,
    plugins,
    protection,
    provider_keys,
    signals,
    strategies,
    system,
)
from .services import provider_keys as provider_key_service
from .services.executor import PaperOrderExecutor
from .services.graph import PaperGraphRuntime
from .services.market_monitor import MarketMonitor
from .services.paper import ensure_genesis, freshness_from_settings
from .services.plugin_runtime import PaperPluginRuntime
from .services.provider_secrets import build_secret_cipher
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
    # Jupiter's documented keyed limit is one request per second. The bounded budget
    # wraps its client so concurrent quotes wait instead of bursting past it.
    provider_budget = ProviderBudgetManager()
    provider_cipher = build_secret_cipher(settings)

    async def jupiter_api_key() -> str | None:
        """Resolve the workspace key per request so create and revoke take effect now."""

        def load() -> str | None:
            try:
                with session_factory()() as session:
                    return provider_key_service.resolve_secret(
                        session,
                        workspace_id=settings.workspace_id,
                        provider="jupiter",
                        cipher=provider_cipher,
                    )
            except provider_key_service.ProviderKeyNotFound:
                return None
            except provider_key_service.ProviderKeyError as exc:
                # A stored key that cannot be decrypted must fail the provider call, not
                # silently downgrade to the keyless endpoint. Retryability follows the
                # store's: a rotated identity or deleted entry never fixes itself.
                raise ProviderKeyUnavailable(
                    "the stored Jupiter key is unavailable", retryable=exc.retryable
                ) from exc

        return await asyncio.to_thread(load)

    jupiter = JupiterClient(budget=provider_budget, api_key_provider=jupiter_api_key)
    app.state.provider_budget = provider_budget
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
    app.state.quote_router = QuoteRouter(
        providers=providers,
        dexscreener=dexscreener,
        freshness=freshness_from_settings(settings),
    )

    def kill_switch_active() -> bool:
        """Read the persisted flag per order so arming applies across processes."""
        with session_factory()() as session:
            return read_kill_switch(session, settings).active

    executor = KillSwitchExecutor(
        PaperOrderExecutor(session_factory(), settings, dexscreener, app.state.quote_router),
        kill_switch_active,
    )
    app.state.order_executor = executor

    # One context builder for the scheduler and the HTTP dry run. It owns candle and
    # ledger reads; the engine only sees the frozen EvalContext contract.
    graph_runtime = PaperGraphRuntime(gecko, dexscreener, settings.data_mode)
    app.state.graph_runtime = graph_runtime

    plugin_registry = PluginRegistry(paths=settings.plugin_path_list)
    plugin_host = PluginHost(
        timeout_seconds=settings.plugin_timeout_seconds,
        memory_mb=settings.plugin_memory_mb,
        cpu_seconds=settings.plugin_cpu_seconds,
    )
    plugin_runtime = PaperPluginRuntime(gecko, dexscreener, settings, plugin_registry)
    app.state.plugin_registry = plugin_registry
    app.state.plugin_host = plugin_host
    app.state.plugin_runtime = plugin_runtime

    scheduler: StrategyScheduler | None = None
    scheduler_task: asyncio.Task[None] | None = None
    if settings.engine_inline:
        scheduler = StrategyScheduler(
            session_factory(),
            dexscreener,
            executor,
            graph_runtime=graph_runtime,
            plugin_runtime=plugin_runtime,
            plugin_host=plugin_host,
            plugin_registry=plugin_registry,
            interval_seconds=settings.engine_interval_seconds,
            max_mark_age_seconds=settings.max_mark_age_seconds,
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
        await plugin_host.close_all()
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
    # 401 still carries CORS headers for the browser. The read limiter runs inside
    # auth so it can see the resolved principal.
    app.add_middleware(ReadRateLimitMiddleware, settings=settings)
    app.add_middleware(
        BearerAuthMiddleware,
        settings=settings,
        session_factory=session_factory(),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    app.add_middleware(ObservabilityMiddleware)

    app.add_exception_handler(RequestValidationError, cast(Any, request_validation_error))

    app.include_router(system.router)
    app.include_router(markets.router)
    app.include_router(signals.router)
    app.include_router(paper.router)
    app.include_router(account.router)
    app.include_router(strategies.router)
    app.include_router(graph.router)
    app.include_router(backtests.router)
    app.include_router(plugins.router)
    app.include_router(agent.router)
    app.include_router(protection.router)
    app.include_router(provider_keys.router)
    return app


app = create_app()
