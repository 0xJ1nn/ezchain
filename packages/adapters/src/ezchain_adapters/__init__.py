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
from .errors import (
    AdapterError,
    CandlesUnsupported,
    NoRoute,
    NotFound,
    ProviderKeyUnavailable,
    RateLimited,
    UpstreamError,
)
from .firehose import FirehoseEvent, FirehoseFeed, normalize_firehose_event
from .geckoterminal import TIMEFRAMES as CANDLE_TIMEFRAMES
from .geckoterminal import GeckoTerminalClient, parse_ohlcv, timeframe_seconds
from .http import JsonApiClient
from .jupiter import SOL_MINT, USDC_MINT, JupiterClient
from .kyberswap import KyberSwapClient
from .lifi import LifiClient
from .osmosis_sqs import OsmosisSqsClient
from .provider_budget import (
    JUPITER_LIMIT,
    ProviderBudgetExhausted,
    ProviderBudgetManager,
    ProviderLimit,
)
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
    "CANDLE_TIMEFRAMES",
    "CHAINS",
    "CandlesUnsupported",
    "ChainCoverage",
    "ChainMeta",
    "CircuitBreaker",
    "CetusClient",
    "DecimalsResolver",
    "DexScreenerClient",
    "FirehoseEvent",
    "FirehoseFeed",
    "GeckoTerminalClient",
    "HttpQuoteProvider",
    "JsonApiClient",
    "JupiterClient",
    "JUPITER_LIMIT",
    "KyberSwapClient",
    "LifiClient",
    "NoRoute",
    "NotFound",
    "OsmosisSqsClient",
    "PAPER_MARK",
    "PROVIDERS",
    "ProviderBudgetExhausted",
    "ProviderBudgetManager",
    "ProviderKeyUnavailable",
    "ProviderLimit",
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
    "normalize_firehose_event",
    "normalize_pair",
    "parse_ohlcv",
    "seed_addresses",
    "snapshot_from_pairs",
    "timeframe_seconds",
]
