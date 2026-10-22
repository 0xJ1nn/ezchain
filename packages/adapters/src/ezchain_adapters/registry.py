"""Chain registry. Capability flags, not chain-name branches, drive the UI.

Every chain carries its quote ladder (``quote_providers``, in preference order) and, when
the ladder can execute, the stable token that sizes a buy and receives a sell. Chain
identity, provider slugs and the pricing stable live here so no other module needs a
``chain == "..."`` branch. Ladders were verified provider-by-provider on 2026-10-22; see
``docs/CHAINS.md`` §7 for the per-provider evidence and ``docs/DECISIONS.md`` ADR-0022.

A ladder whose head is ``paper_mark`` means no keyless executable quote exists for that
chain: it keeps the capped DexScreener mark (ADR-0020) and ``live_execute`` stays false.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ezchain_core import QuoteKind

# The non-executable mark. It is a QuoteKind, not a venue: a chain may list it as its whole
# ladder, and no chain may list it after an executable provider (a failed executable quote
# must fail closed, never silently degrade into a mark).
PAPER_MARK: Final[QuoteKind] = "paper_mark"

KYBERSWAP = "kyberswap"
VELORA = "velora"
LIFI = "lifi"
JUPITER = "jupiter"
CETUS = "cetus"
STONFI = "stonfi"
OSMOSIS_SQS = "osmosis_sqs"

# Every provider id the router can dispatch to.
PROVIDERS: tuple[str, ...] = (
    KYBERSWAP,
    VELORA,
    LIFI,
    JUPITER,
    CETUS,
    STONFI,
    OSMOSIS_SQS,
)

# Ladders, one per chain, from the verified free-source table. KyberSwap covers the eight
# registered EVM majors plus Linea; Velora backs the eight it still lists; LI.FI is the only
# free source for Mantle, Scroll, zkSync, Cronos, Celo and Metis (75 requests / 2 h, so it
# is never a primary where something cheaper exists). Sonic is supported by KyberSwap and
# verified live but is not a registered chain, so it has no ladder here.
_EVM_MAJORS: tuple[str, ...] = (KYBERSWAP, VELORA, LIFI)


@dataclass(frozen=True)
class QuoteAsset:
    """The stable a buy pays with and a sell receives, in that chain's token units."""

    address: str
    symbol: str
    decimals: int


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
    # Ordered quote ladder. Empty means the mark-only default is applied in __post_init__.
    quote_providers: tuple[str, ...] = ()
    # None on chains without an executable ladder: there is nothing to price against.
    quote_asset: QuoteAsset | None = None
    evm_chain_id: int | None = None
    geckoterminal_id: str | None = None
    venues: tuple[str, ...] = ()
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.quote_providers:
            object.__setattr__(self, "quote_providers", (PAPER_MARK,))
        unknown = [p for p in self.quote_providers if p != PAPER_MARK and p not in PROVIDERS]
        if unknown:  # pragma: no cover - import-time guard against a typo in the ladder
            raise ValueError(f"{self.id}: unknown quote providers {unknown}")
        if PAPER_MARK in self.quote_providers and len(self.quote_providers) > 1:
            raise ValueError(f"{self.id}: paper_mark cannot follow an executable provider")
        if self.quote_kind != PAPER_MARK and self.quote_asset is None:
            raise ValueError(f"{self.id}: an executable ladder needs a quote_asset")

    @property
    def quote_kind(self) -> str:
        """The primary provider — the one the UI badge names."""
        return self.quote_providers[0]

    @property
    def executable_quotes(self) -> bool:
        return self.quote_kind != PAPER_MARK

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
                "quote_providers": list(self.quote_providers),
                "executable_quotes": self.executable_quotes,
            },
            "venues": list(self.venues),
            "notes": self.notes,
        }


def _usdc(address: str, *, decimals: int = 6) -> QuoteAsset:
    return QuoteAsset(address=address, symbol="USDC", decimals=decimals)


def _usdt(address: str, *, decimals: int = 6) -> QuoteAsset:
    return QuoteAsset(address=address, symbol="USDT", decimals=decimals)


CHAINS: tuple[ChainMeta, ...] = (
    ChainMeta(
        "solana",
        "Solana",
        "svm",
        "solana",
        "SOL",
        "T1",
        quote_providers=(JUPITER,),
        quote_asset=_usdc("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"),
        geckoterminal_id="solana",
        venues=("jupiter",),
    ),
    ChainMeta(
        "ethereum",
        "Ethereum",
        "evm",
        "ethereum",
        "ETH",
        "T1",
        quote_providers=_EVM_MAJORS,
        quote_asset=_usdc("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"),
        evm_chain_id=1,
        geckoterminal_id="eth",
    ),
    ChainMeta(
        "base",
        "Base",
        "evm",
        "base",
        "ETH",
        "T1",
        quote_providers=_EVM_MAJORS,
        quote_asset=_usdc("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"),
        evm_chain_id=8453,
        geckoterminal_id="base",
    ),
    ChainMeta(
        "arbitrum",
        "Arbitrum One",
        "evm",
        "arbitrum",
        "ETH",
        "T1",
        quote_providers=_EVM_MAJORS,
        quote_asset=_usdc("0xaf88d065e77c8cC2239327C5EDb3A432268e5831"),
        evm_chain_id=42161,
        geckoterminal_id="arbitrum",
    ),
    ChainMeta(
        "polygon",
        "Polygon PoS",
        "evm",
        "polygon",
        "POL",
        "T1",
        quote_providers=_EVM_MAJORS,
        quote_asset=_usdc("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"),
        evm_chain_id=137,
        geckoterminal_id="polygon_pos",
    ),
    ChainMeta(
        "bsc",
        "BNB Chain",
        "evm",
        "bsc",
        "BNB",
        "T1",
        quote_providers=_EVM_MAJORS,
        # Binance-Peg USDC carries 18 decimals, unlike every other chain's USDC.
        quote_asset=_usdc("0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", decimals=18),
        evm_chain_id=56,
        geckoterminal_id="bsc",
    ),
    ChainMeta(
        "optimism",
        "Optimism",
        "evm",
        "optimism",
        "ETH",
        "T2",
        quote_providers=_EVM_MAJORS,
        quote_asset=_usdc("0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85"),
        evm_chain_id=10,
        geckoterminal_id="optimism",
    ),
    ChainMeta(
        "avalanche",
        "Avalanche C",
        "evm",
        "avalanche",
        "AVAX",
        "T2",
        quote_providers=_EVM_MAJORS,
        quote_asset=_usdc("0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E"),
        evm_chain_id=43114,
        geckoterminal_id="avax",
    ),
    ChainMeta(
        "blast",
        "Blast",
        "evm",
        "blast",
        "ETH",
        evm_chain_id=81457,
        geckoterminal_id="blast",
    ),
    ChainMeta(
        "mantle",
        "Mantle",
        "evm",
        "mantle",
        "MNT",
        quote_providers=(LIFI,),
        quote_asset=_usdc("0x09Bc4E0D864854c6aFB6eB9A9cdF58aC190D0dF9"),
        evm_chain_id=5000,
        geckoterminal_id="mantle",
    ),
    ChainMeta(
        "linea",
        "Linea",
        "evm",
        "linea",
        "ETH",
        quote_providers=(KYBERSWAP, LIFI),
        quote_asset=_usdc("0x176211869cA2b568f2A7D4EE941E073a821EE1ff"),
        evm_chain_id=59144,
        geckoterminal_id="linea",
    ),
    ChainMeta(
        "scroll",
        "Scroll",
        "evm",
        "scroll",
        "ETH",
        quote_providers=(LIFI,),
        quote_asset=_usdc("0x06eFdBFf2a14a7c8E15944D1F4A48F9F95F663A4"),
        evm_chain_id=534352,
        geckoterminal_id="scroll",
    ),
    ChainMeta(
        "zksync",
        "zkSync Era",
        "evm",
        "zksync",
        "ETH",
        quote_providers=(LIFI,),
        quote_asset=_usdc("0x1d17CBcF0D6D143135aE902365D2E5e2A16538D4"),
        evm_chain_id=324,
        geckoterminal_id="zksync",
    ),
    ChainMeta(
        "sui",
        "Sui",
        "move",
        "sui",
        "SUI",
        quote_providers=(CETUS,),
        quote_asset=_usdc(
            "0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC"
        ),
        geckoterminal_id="sui-network",
    ),
    ChainMeta("aptos", "Aptos", "move", "aptos", "APT", geckoterminal_id="aptos"),
    ChainMeta(
        "ton",
        "TON",
        "tvm",
        "ton",
        "TON",
        quote_providers=(STONFI,),
        # TON has no USDC pool worth quoting; the deepest stable is the USDT jetton.
        quote_asset=_usdt("EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"),
        geckoterminal_id="ton",
    ),
    ChainMeta("tron", "Tron", "tvm", "tron", "TRX", geckoterminal_id="tron"),
    ChainMeta("near", "NEAR", "near", "near", "NEAR", geckoterminal_id="near"),
    ChainMeta(
        "osmosis",
        "Osmosis",
        "cosmos",
        "osmosis",
        "OSMO",
        quote_providers=(OSMOSIS_SQS,),
        quote_asset=_usdc("ibc/27394FB092D2ECCD56123C74F36E4C1F926001CEADA9CA97EA622B25F41E5EB2"),
        geckoterminal_id=None,
    ),
    ChainMeta("cosmos", "Cosmos Hub", "cosmos", "cosmos", "ATOM", geckoterminal_id=None),
    ChainMeta("fantom", "Fantom", "evm", "fantom", "FTM", evm_chain_id=250, geckoterminal_id="ftm"),
    ChainMeta(
        "cronos",
        "Cronos",
        "evm",
        "cronos",
        "CRO",
        quote_providers=(LIFI,),
        quote_asset=_usdc("0xc21223249CA28397B4B6541dfFaEcC539BfF0c59"),
        evm_chain_id=25,
        geckoterminal_id="cro",
    ),
    ChainMeta(
        "celo",
        "Celo",
        "evm",
        "celo",
        "CELO",
        quote_providers=(LIFI,),
        quote_asset=_usdc("0xcebA9300f2b948710d2653dD7B07f33A8B32118C"),
        evm_chain_id=42220,
        geckoterminal_id="celo",
    ),
    ChainMeta(
        "moonbeam",
        "Moonbeam",
        "evm",
        "moonbeam",
        "GLMR",
        evm_chain_id=1284,
        geckoterminal_id="glmr",
    ),
    ChainMeta(
        "metis",
        "Metis",
        "evm",
        "metis",
        "METIS",
        quote_providers=(LIFI,),
        # Metis has no USDC; m.USDC is the bridged stable LI.FI lists.
        quote_asset=_usdc("0xEA32A96608495e54156Ae48931A7c20f0dcc1a21"),
        evm_chain_id=1088,
        geckoterminal_id="metis",
    ),
)

BY_ID = {c.id: c for c in CHAINS}
BY_DEXSCREENER = {c.dexscreener_id: c for c in CHAINS}


def get_chain(chain_id: str) -> ChainMeta | None:
    return BY_ID.get(chain_id)


def chain_for_dexscreener(chain_id: str) -> ChainMeta | None:
    return BY_DEXSCREENER.get(chain_id)
