"""Chain-specific seed tokens for discovery.

DexScreener's discovery endpoints (profiles, boosts, takeovers) are dominated by Solana
tokens. Without seeds, ``chain=all`` is effectively a Solana board. The seeds below are
canonical, high-liquidity tokens per chain, so a whole-market browse always has coverage
on the chains ezCHAIN advertises.

Seeds are only used for discovery (no search query); they never produce a price without a
real pair returned by the API, and an absent chain simply yields no rows.

Recorded against the public DexScreener API on 2026-10-22. Solana uses its four canonical
majors (wrapped SOL, USDC, BONK, JUP) rather than a promotional-feed hint, so an empty
profile/boost/takeover response cannot erase the chain from an all-chain browse. Osmosis,
Cosmos Hub and Moonbeam returned no indexed pairs that day, so their seeds are canonical
on-chain identifiers (``uosmo``, ``uatom``, WGLMR) rather than verified API hits; they stay
so discovery picks the chains up if DexScreener coverage returns.

The Blast, Mantle, Linea, Scroll and zkSync seeds were verified the same day with
``/latest/dex/tokens``: every address below returned indexed pairs on its own chain
(Blast WETH/USDB, Mantle WETH/WMNT, Linea and Scroll WETH/USDC, zkSync WETH/USDC).
"""

from __future__ import annotations

from .registry import BY_ID

SEED_TOKENS: dict[str, tuple[str, ...]] = {
    "ethereum": (
        "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # WETH
        "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",  # USDC
    ),
    "base": (
        "0x4200000000000000000000000000000000000006",  # WETH (also Optimism's WETH)
        "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",  # USDC
    ),
    "arbitrum": (
        "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",  # WETH
        "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC
    ),
    "polygon": (
        "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",  # WETH
        "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",  # USDC
    ),
    "optimism": (
        "0x4200000000000000000000000000000000000006",  # WETH (deduped against Base)
        "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",  # USDC
    ),
    "bsc": (
        "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",  # WBNB
        "0x55d398326f99059fF775485246999027B3197955",  # USDT
    ),
    "avalanche": (
        "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7",  # WAVAX
        "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",  # USDC
    ),
    "solana": (
        "So11111111111111111111111111111111111111112",  # SOL (wrapped)
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
        "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
        "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",  # JUP
    ),
    "sui": (
        "0x2::sui::SUI",  # SUI
        "0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC",
    ),
    "aptos": (
        "0x1::aptos_coin::AptosCoin",  # APT
        "0xbae207659db88bea0cbead6da0ed00aac12edcdda169e591cd41c94180b46f3b",  # USDC
    ),
    "ton": (
        "EQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAM9c",  # native TON
        "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs",  # USDT jetton
    ),
    "tron": (
        "TNUC9Qb1rRpS5CbWLmNMxXBjyFoydXjWFR",  # WTRX
        "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",  # USDT (TRC-20)
    ),
    "near": (
        "wrap.near",  # wNEAR
        "17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1",  # USDC
    ),
    "blast": (
        "0x4300000000000000000000000000000000000004",  # WETH
        "0x4300000000000000000000000000000000000003",  # USDB
    ),
    "mantle": (
        "0xdEAddEaDdeadDEadDEADDEAddEADDEAddead1111",  # WETH
        "0x78c1b0C915c4FAA5FffA6CAbf0219DA63d7f4cb8",  # WMNT
    ),
    "linea": (
        "0xe5D7C2a44FfDDf6b295A15c148167daaAf5Cf34f",  # WETH
        "0x176211869cA2b568f2A7D4EE941E073a821EE1ff",  # USDC
    ),
    "scroll": (
        "0x5300000000000000000000000000000000000004",  # WETH
        "0x06eFdBFf2a14a7c8E15944D1F4A48F9F95F663A4",  # USDC
    ),
    "zksync": (
        "0x5AEa5775959fBC2557Cc8789bC1bf90A239D9a91",  # WETH
        "0x1d17CBcF0D6D143135aE902365D2E5e2A16538D4",  # USDC
    ),
    "osmosis": ("uosmo",),  # OSMO
    "cosmos": ("uatom",),  # ATOM
    "fantom": (
        "0x21be370D5312f44cB42ce377BC9b8a0cEF1A4C83",  # WFTM
        "0x04068DA6C83AFCFA0e13ba15A6696662335D5B75",  # USDC
    ),
    "cronos": (
        "0x5C7F8A570d578ED84E63fdFA7b1eE72dEae1AE23",  # WCRO
        "0xc21223249CA28397B4B6541dfFaEcC539BfF0c59",  # USDC
    ),
    "celo": (
        "0x471EcE3750Da237f93B8E339c536989b8978a438",  # CELO
        "0xcebA9300f2b948710d2653dD7B07f33A8B32118C",  # USDC
    ),
    "moonbeam": ("0xAcc15dC74880C9944775448304B263D191c6077F",),  # WGLMR
    "metis": (
        "0xDeadDeAddeAddEAddeadDEaDDEAdDeaDDeAD0000",  # METIS
        "0xEA32A96608495e54156Ae48931A7c20f0dcc1a21",  # m.USDC
    ),
}

_MISSING_CHAINS = set(SEED_TOKENS) - set(BY_ID)
if _MISSING_CHAINS:  # pragma: no cover - import-time registry guard
    raise RuntimeError(f"seed tokens reference unknown chains: {sorted(_MISSING_CHAINS)}")


def seed_addresses(chain: str | None) -> tuple[str, ...]:
    """Return seed token addresses for one chain, or a flat deduped list for all chains.

    ``chain=all`` discovery does not use the flat list: it buckets seeds per chain so a
    shared address (WETH on Base and Optimism) is requested in each chain's own lookup.
    The flat list remains for callers that only need the union.
    """
    if chain and chain != "all":
        return SEED_TOKENS.get(chain, ())
    ordered: list[str] = []
    seen: set[str] = set()
    for addresses in SEED_TOKENS.values():
        for address in addresses:
            key = address.lower()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(address)
    return tuple(ordered)
