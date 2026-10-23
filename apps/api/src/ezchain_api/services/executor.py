"""Composition adapter: expose the paper order service through the engine port."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from ezchain_adapters import DexScreenerClient, QuoteRouter
from ezchain_core import OrderRequest
from ezchain_engine import OrderRejected

from ..config import Settings
from . import paper


class PaperOrderExecutor:
    """One session per intent; the paper service owns idempotency and the ledger."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        settings: Settings,
        dexscreener: DexScreenerClient,
        quotes: QuoteRouter,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._dexscreener = dexscreener
        self._quotes = quotes

    async def place_order(self, request: OrderRequest) -> str:
        with self._session_factory() as session:
            row = await paper.place_order(
                session,
                request,
                self._settings,
                self._dexscreener,
                self._quotes,
            )
            if row.status != "filled":
                raise OrderRejected(row.reason, order_id=row.id)
            return row.id
