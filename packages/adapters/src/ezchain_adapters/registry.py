"""Chain registry. Capability flags, not chain-name branches, drive the UI.

A chain's identity at each provider lives here: ``dexscreener_id`` for markets and
``geckoterminal_id`` for candle history. A chain with no GeckoTerminal network has no
OHLCV provider, so it reports ``candles = false`` and core code fails closed instead of
guessing a network id.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChainMeta:
    id: str
    name: str
    family: str
    dexscreener_id: str
    native_asset: str
    tier: str = "T2"
    browse: bool = True
    paper_execute: bool = True
    live_execute: bool = False
    quote_kind: str = "paper_mark"
    venues: tuple[str, ...] = ()
    notes: str = ""
    # GeckoTerminal network id. ``None`` means this chain has no OHLCV provider.
    geckoterminal_id: str | None = None

    @property
    def candles(self) -> bool:
        """Candle history is a capability, derived from having a provider network."""
        return self.geckoterminal_id is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "family": self.family,
            "native_asset": self.native_asset,
            "tier": self.tier,
            "capabilities": {
                "browse": self.browse,
                "paper_execute": self.paper_execute,
                "live_execute": self.live_execute,
                "quote_kind": self.quote_kind,
                "candles": self.candles,
            },
            "venues": list(self.venues),
            "notes": self.notes,
        }


CHAINS: tuple[ChainMeta, ...] = (
    ChainMeta(
        "solana",
        "Solana",
        "svm",
        "solana",
        "SOL",
        "T1",
        quote_kind="jupiter",
        venues=("jupiter",),
        geckoterminal_id="solana",
    ),
    ChainMeta("ethereum", "Ethereum", "evm", "ethereum", "ETH", geckoterminal_id="eth"),
    ChainMeta("base", "Base", "evm", "base", "ETH", geckoterminal_id="base"),
    ChainMeta("arbitrum", "Arbitrum One", "evm", "arbitrum", "ETH", geckoterminal_id="arbitrum"),
    ChainMeta("polygon", "Polygon PoS", "evm", "polygon", "POL", geckoterminal_id="polygon_pos"),
    ChainMeta("bsc", "BNB Chain", "evm", "bsc", "BNB", geckoterminal_id="bsc"),
    ChainMeta("optimism", "Optimism", "evm", "optimism", "ETH", geckoterminal_id="optimism"),
    ChainMeta("avalanche", "Avalanche C", "evm", "avalanche", "AVAX", geckoterminal_id="avax"),
    ChainMeta("blast", "Blast", "evm", "blast", "ETH", geckoterminal_id="blast"),
    ChainMeta("mantle", "Mantle", "evm", "mantle", "MNT", geckoterminal_id="mantle"),
    ChainMeta("linea", "Linea", "evm", "linea", "ETH", geckoterminal_id="linea"),
    ChainMeta("scroll", "Scroll", "evm", "scroll", "ETH", geckoterminal_id="scroll"),
    ChainMeta("zksync", "zkSync Era", "evm", "zksync", "ETH", geckoterminal_id="zksync"),
    ChainMeta("sui", "Sui", "move", "sui", "SUI", geckoterminal_id="sui-network"),
    ChainMeta("aptos", "Aptos", "move", "aptos", "APT", geckoterminal_id="aptos"),
    ChainMeta("ton", "TON", "tvm", "ton", "TON", geckoterminal_id="ton"),
    ChainMeta("tron", "Tron", "tvm", "tron", "TRX", geckoterminal_id="tron"),
    ChainMeta("near", "NEAR", "near", "near", "NEAR", geckoterminal_id="near"),
    # GeckoTerminal has no Osmosis or Cosmos Hub network: these chains have no OHLCV.
    ChainMeta("osmosis", "Osmosis", "cosmos", "osmosis", "OSMO"),
    ChainMeta("cosmos", "Cosmos Hub", "cosmos", "cosmos", "ATOM"),
    ChainMeta("fantom", "Fantom", "evm", "fantom", "FTM", geckoterminal_id="ftm"),
    ChainMeta("cronos", "Cronos", "evm", "cronos", "CRO", geckoterminal_id="cro"),
    ChainMeta("celo", "Celo", "evm", "celo", "CELO", geckoterminal_id="celo"),
    ChainMeta("moonbeam", "Moonbeam", "evm", "moonbeam", "GLMR", geckoterminal_id="glmr"),
    ChainMeta("metis", "Metis", "evm", "metis", "METIS", geckoterminal_id="metis"),
)

BY_ID = {c.id: c for c in CHAINS}
BY_DEXSCREENER = {c.dexscreener_id: c for c in CHAINS}
BY_GECKOTERMINAL = {c.geckoterminal_id: c for c in CHAINS if c.geckoterminal_id is not None}


def get_chain(chain_id: str) -> ChainMeta | None:
    return BY_ID.get(chain_id)


def chain_for_dexscreener(chain_id: str) -> ChainMeta | None:
    return BY_DEXSCREENER.get(chain_id)
