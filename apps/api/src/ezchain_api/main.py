"""ezCHAIN API entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ezchain_adapters import DexScreenerClient, JupiterClient

from .config import Settings, get_settings
from .db import create_schema, session_factory
from .routers import markets, paper, strategies, system
from .services.paper import ensure_genesis


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()
    if settings.auto_create_schema:
        create_schema()
    with session_factory()() as session:
        ensure_genesis(session, settings.paper_starting_cash_usd)

    dexscreener = DexScreenerClient()
    jupiter = JupiterClient()
    await dexscreener.__aenter__()
    await jupiter.__aenter__()
    app.state.dexscreener = dexscreener
    app.state.jupiter = jupiter

    yield

    await dexscreener.__aexit__(None, None, None)
    await jupiter.__aexit__(None, None, None)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="ezCHAIN API",
        version="0.1.0",
        description="Live market browsing and paper execution. No keys, no live money.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    app.include_router(system.router)
    app.include_router(markets.router)
    app.include_router(paper.router)
    app.include_router(strategies.router)
    return app


app = create_app()
