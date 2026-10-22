"""ezCHAIN adapters."""

from .breaker import CircuitBreaker
from .cetus import CetusClient
from .decimals import DecimalsResolver
from .dexscreener import (
    ChainCoverage,
    DexScreenerClient,
    SortMode,
    normalize_pair,
    snapshot_from_pairs,
)
from .errors import AdapterError, NoRoute, NotFound, RateLimited, UpstreamError
from .geckoterminal import GeckoTerminalClient
from .http import JsonApiClient
from .jupiter import SOL_MINT, USDC_MINT, JupiterClient
from .kyberswap import KyberSwapClient
from .lifi import LifiClient
from .osmosis_sqs import OsmosisSqsClient
from .quotes import HttpQuoteProvider, QuoteProvider, QuoteRequest, QuoteRouter
from .registry import (
    BY_ID,
    CHAINS,
    PAPER_MARK,
    PROVIDERS,
    ChainMeta,
    QuoteAsset,
    get_chain,
)
from .seeds import SEED_TOKENS, seed_addresses
from .stonfi import StonfiClient
from .velora import VeloraClient

__all__ = [
    "AdapterError",
    "BY_ID",
    "CHAINS",
    "ChainCoverage",
    "ChainMeta",
    "CircuitBreaker",
    "CetusClient",
    "DecimalsResolver",
    "DexScreenerClient",
    "GeckoTerminalClient",
    "HttpQuoteProvider",
    "JupiterClient",
    "JsonApiClient",
    "KyberSwapClient",
    "LifiClient",
    "NoRoute",
    "NotFound",
    "OsmosisSqsClient",
    "PAPER_MARK",
    "PROVIDERS",
    "QuoteAsset",
    "QuoteProvider",
    "QuoteRequest",
    "QuoteRouter",
    "RateLimited",
    "SEED_TOKENS",
    "SOL_MINT",
    "SortMode",
    "StonfiClient",
    "USDC_MINT",
    "UpstreamError",
    "VeloraClient",
    "get_chain",
    "normalize_pair",
    "seed_addresses",
    "snapshot_from_pairs",
]
