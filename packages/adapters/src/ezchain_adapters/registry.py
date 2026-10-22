"""Chain registry. Capability flags, not chain-name branches, drive the UI."""

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
            },
            "venues": list(self.venues),
            "notes": self.notes,
        }


CHAINS: tuple[ChainMeta, ...] = (
    ChainMeta(
        "solana", "Solana", "svm", "solana", "SOL", "T1", quote_kind="jupiter", venues=("jupiter",)
    ),
    ChainMeta("ethereum", "Ethereum", "evm", "ethereum", "ETH"),
    ChainMeta("base", "Base", "evm", "base", "ETH"),
    ChainMeta("arbitrum", "Arbitrum One", "evm", "arbitrum", "ETH"),
    ChainMeta("polygon", "Polygon PoS", "evm", "polygon", "POL"),
    ChainMeta("bsc", "BNB Chain", "evm", "bsc", "BNB"),
    ChainMeta("optimism", "Optimism", "evm", "optimism", "ETH"),
    ChainMeta("avalanche", "Avalanche C", "evm", "avalanche", "AVAX"),
    ChainMeta("blast", "Blast", "evm", "blast", "ETH"),
    ChainMeta("mantle", "Mantle", "evm", "mantle", "MNT"),
    ChainMeta("linea", "Linea", "evm", "linea", "ETH"),
    ChainMeta("scroll", "Scroll", "evm", "scroll", "ETH"),
    ChainMeta("zksync", "zkSync Era", "evm", "zksync", "ETH"),
    ChainMeta("sui", "Sui", "move", "sui", "SUI"),
    ChainMeta("aptos", "Aptos", "move", "aptos", "APT"),
    ChainMeta("ton", "TON", "tvm", "ton", "TON"),
    ChainMeta("tron", "Tron", "tvm", "tron", "TRX"),
    ChainMeta("near", "NEAR", "near", "near", "NEAR"),
    ChainMeta("osmosis", "Osmosis", "cosmos", "osmosis", "OSMO"),
    ChainMeta("cosmos", "Cosmos Hub", "cosmos", "cosmos", "ATOM"),
    ChainMeta("fantom", "Fantom", "evm", "fantom", "FTM"),
    ChainMeta("cronos", "Cronos", "evm", "cronos", "CRO"),
    ChainMeta("celo", "Celo", "evm", "celo", "CELO"),
    ChainMeta("moonbeam", "Moonbeam", "evm", "moonbeam", "GLMR"),
    ChainMeta("metis", "Metis", "evm", "metis", "METIS"),
)

BY_ID = {c.id: c for c in CHAINS}
BY_DEXSCREENER = {c.dexscreener_id: c for c in CHAINS}


def get_chain(chain_id: str) -> ChainMeta | None:
    return BY_ID.get(chain_id)


def chain_for_dexscreener(chain_id: str) -> ChainMeta | None:
    return BY_DEXSCREENER.get(chain_id)
