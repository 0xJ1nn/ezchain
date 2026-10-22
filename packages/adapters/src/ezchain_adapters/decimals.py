"""Token decimals for raw quote amounts, resolved once and failing closed.

Every provider quotes in raw integer units, so a quote is meaningless without decimals.
Resolution order:

1. the chain's own pricing stable (``ChainMeta.quote_asset``) — the registry is its home;
2. a value the provider itself reported for that token, remembered here;
3. GeckoTerminal metadata, cached for the process lifetime of the entry;
4. nothing — raise ``NotFound``. An unknown decimals is never guessed: guessing scales the
   amount by a wrong power of ten, which is a fabricated fill.
"""

from __future__ import annotations

from collections import OrderedDict

from .errors import NotFound
from .geckoterminal import GeckoTerminalClient
from .registry import ChainMeta

DECIMALS_CACHE_MAX_ENTRIES = 512


class DecimalsResolver:
    def __init__(
        self,
        gecko: GeckoTerminalClient | None = None,
        *,
        max_entries: int = DECIMALS_CACHE_MAX_ENTRIES,
    ) -> None:
        self._gecko = gecko
        self._max_entries = max_entries
        self._cache: OrderedDict[tuple[str, str], int] = OrderedDict()

    def remember(self, chain_id: str, address: str, decimals: int) -> None:
        """Record a provider-reported decimals so later quotes skip the lookup."""
        if not 0 <= decimals <= 36:
            return
        key = (chain_id, address)
        self._cache[key] = decimals
        self._cache.move_to_end(key)
        while len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)

    async def decimals(self, chain: ChainMeta, address: str) -> int:
        asset = chain.quote_asset
        if asset is not None and address == asset.address:
            return asset.decimals
        key = (chain.id, address)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached
        if self._gecko is None or chain.geckoterminal_id is None:
            raise NotFound(f"no decimals source for {address} on {chain.id}")
        decimals = await self._gecko.token_decimals(chain.geckoterminal_id, address)
        self.remember(chain.id, address, decimals)
        return decimals
