"""Adapter protocols. Implementations live in sibling modules."""

from __future__ import annotations

from typing import Protocol

from ezchain_core import MarketRow, Quote, SourceRef, TokenSnapshot


class MarketDataAdapter(Protocol):
    async def list_markets(
        self, *, chain: str | None, query: str | None, limit: int
    ) -> tuple[list[MarketRow], SourceRef]: ...

    async def get_token(self, chain: str, address: str) -> TokenSnapshot: ...


class VenueAdapter(Protocol):
    async def quote(
        self, *, chain: str, token_address: str, side: str, amount_usd: str
    ) -> Quote: ...
