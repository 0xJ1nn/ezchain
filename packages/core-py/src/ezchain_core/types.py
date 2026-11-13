"""Shared domain types for the ezCHAIN paper slice.

Money is a decimal USD string at the API boundary; prices, fees and token quantities use
Decimal inside the domain. No floats cross a money path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

Mode = Literal["paper", "live"]
Side = Literal["buy", "sell"]
# One literal per executable quote provider plus the non-executable DexScreener mark.
# The registry's per-chain ladder decides which of these a chain may produce.
QuoteKind = Literal[
    "jupiter",
    "kyberswap",
    "velora",
    "lifi",
    "cetus",
    "stonfi",
    "osmosis_sqs",
    "paper_mark",
]
StrategyKind = Literal["momentum", "dip", "dca", "threshold", "graph", "plugin"]
StrategyStatus = Literal["active", "paused"]


def utcnow() -> datetime:
    return datetime.now(UTC)


# How far an observation may run ahead of the reader's clock before it stops being a
# tolerated skew and becomes a clock fault. One constant for the writer, the reader and
# the risk gate, so a source cannot be trusted by one surface and faulted by another.
MARK_FUTURE_SKEW_SECONDS = 5.0


@dataclass(frozen=True)
class SourceRef:
    name: str
    url: str | None
    observed_at: datetime

    def _observed(self) -> datetime:
        return (
            self.observed_at.astimezone(UTC)
            if self.observed_at.tzinfo is not None
            else self.observed_at.replace(tzinfo=UTC)
        )

    def age_seconds(self, now: datetime | None = None) -> float:
        return max(0.0, ((now or utcnow()) - self._observed()).total_seconds())

    def future_skew_seconds(self, now: datetime | None = None) -> float:
        """Seconds this observation runs ahead of the clock; negative when it is past."""
        return ((self._observed()) - (now or utcnow())).total_seconds()

    def is_future(
        self, max_skew_seconds: float = MARK_FUTURE_SKEW_SECONDS, now: datetime | None = None
    ) -> bool:
        """True when the timestamp is further ahead than the tolerated clock skew."""
        return self.future_skew_seconds(now) > max_skew_seconds

    def is_stale(self, max_age_seconds: float = 60.0, now: datetime | None = None) -> bool:
        return self.age_seconds(now) > max_age_seconds

    def to_dict(self) -> dict[str, Any]:
        observed = (
            self.observed_at.astimezone(UTC)
            if self.observed_at.tzinfo is not None
            else self.observed_at.replace(tzinfo=UTC)
        )
        return {
            "name": self.name,
            "url": self.url,
            "observed_at": observed.isoformat(),
        }


def _aware(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class MarketRow:
    chain: str
    pair_address: str
    dex: str
    base_token: str
    quote_token: str
    symbol: str
    name: str
    price_usd: Decimal
    liquidity_usd: Decimal | None
    volume_24h_usd: Decimal | None
    price_change_m5: Decimal | None
    price_change_h1: Decimal | None
    price_change_h6: Decimal | None
    price_change_h24: Decimal | None
    pair_created_at: datetime | None
    url: str | None
    source: SourceRef
    quote_symbol: str = "?"
    quote_name: str = "Unknown"
    price_native: Decimal | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain": self.chain,
            "pair_address": self.pair_address,
            "dex": self.dex,
            "base_token": self.base_token,
            "quote_token": self.quote_token,
            "symbol": self.symbol,
            "name": self.name,
            "quote_symbol": self.quote_symbol,
            "quote_name": self.quote_name,
            "price_native": None if self.price_native is None else str(self.price_native),
            "price_usd": str(self.price_usd),
            "liquidity_usd": None if self.liquidity_usd is None else str(self.liquidity_usd),
            "volume_24h_usd": None if self.volume_24h_usd is None else str(self.volume_24h_usd),
            "price_change_m5": None if self.price_change_m5 is None else str(self.price_change_m5),
            "price_change_h1": None if self.price_change_h1 is None else str(self.price_change_h1),
            "price_change_h6": None if self.price_change_h6 is None else str(self.price_change_h6),
            "price_change_h24": None
            if self.price_change_h24 is None
            else str(self.price_change_h24),
            "pair_created_at": None
            if self.pair_created_at is None
            else self.pair_created_at.isoformat(),
            "url": self.url,
            "source": self.source.to_dict(),
        }


@dataclass(frozen=True)
class PairInfo:
    pair_address: str
    dex: str
    quote_token: str
    price_usd: Decimal
    liquidity_usd: Decimal | None
    volume_24h_usd: Decimal | None
    url: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair_address": self.pair_address,
            "dex": self.dex,
            "quote_token": self.quote_token,
            "price_usd": str(self.price_usd),
            "liquidity_usd": None if self.liquidity_usd is None else str(self.liquidity_usd),
            "volume_24h_usd": None if self.volume_24h_usd is None else str(self.volume_24h_usd),
            "url": self.url,
        }


@dataclass(frozen=True)
class TokenSnapshot:
    chain: str
    address: str
    symbol: str
    name: str
    price_usd: Decimal | None
    liquidity_usd: Decimal | None
    volume_24h_usd: Decimal | None
    price_change_m5: Decimal | None
    price_change_h1: Decimal | None
    price_change_h6: Decimal | None
    price_change_h24: Decimal | None
    pairs: tuple[PairInfo, ...] = ()
    safety: dict[str, Any] = field(default_factory=dict)
    source: SourceRef | None = None
    stale: bool = False
    # Creation time of the deepest pair the snapshot was taken from. None means the
    # source did not report it; risk treats that as unknown, never as an old pair.
    pair_created_at: datetime | None = None
    # Pair identity carried over from the observation. Null when the source did not
    # report it; never inferred from the symbol or the token address.
    pair_address: str | None = None
    dex: str | None = None
    base_token_address: str | None = None
    quote_token_address: str | None = None
    url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain": self.chain,
            "address": self.address,
            "symbol": self.symbol,
            "name": self.name,
            "price_usd": None if self.price_usd is None else str(self.price_usd),
            "liquidity_usd": None if self.liquidity_usd is None else str(self.liquidity_usd),
            "volume_24h_usd": None if self.volume_24h_usd is None else str(self.volume_24h_usd),
            "price_change_m5": None if self.price_change_m5 is None else str(self.price_change_m5),
            "price_change_h1": None if self.price_change_h1 is None else str(self.price_change_h1),
            "price_change_h6": None if self.price_change_h6 is None else str(self.price_change_h6),
            "price_change_h24": None
            if self.price_change_h24 is None
            else str(self.price_change_h24),
            "pairs": [p.to_dict() for p in self.pairs],
            "safety": self.safety,
            "source": None if self.source is None else self.source.to_dict(),
            "stale": self.stale,
            "pair_created_at": None
            if self.pair_created_at is None
            else self.pair_created_at.isoformat(),
            "pair_address": self.pair_address,
            "dex": self.dex,
            "base_token_address": self.base_token_address,
            "quote_token_address": self.quote_token_address,
            "url": self.url,
            "pair_age_seconds": None
            if self.pair_created_at is None or self.source is None
            else max(
                0,
                int(
                    (_aware(self.source.observed_at) - _aware(self.pair_created_at)).total_seconds()
                ),
            ),
        }


@dataclass(frozen=True)
class Candle:
    """One OHLCV bar of a token's history.

    ``open_time`` is the bar's start on the timeframe grid and is always UTC.
    ``timeframe_seconds`` travels with the bar so a consumer (indicator, backtest) never
    has to guess the bar length or re-derive it from a label. ``volume`` is the quote
    (USD) volume when the source reports it; ``None`` means unknown, never zero.

    ``close_time`` is exclusive: a bar is closed once ``close_time <= now``. A bar whose
    ``close_time`` is still in the future is the *forming* bar and must not be used to
    compute an indicator or a backtest return.
    """

    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None
    timeframe_seconds: int

    @property
    def close_time(self) -> datetime:
        """Bar end (exclusive)."""
        start = (
            self.open_time.astimezone(UTC)
            if self.open_time.tzinfo is not None
            else self.open_time.replace(tzinfo=UTC)
        )
        return start + timedelta(seconds=self.timeframe_seconds)

    def is_closed(self, now: datetime) -> bool:
        return self.close_time <= now

    def to_dict(self) -> dict[str, Any]:
        return {
            "open_time": self.open_time.isoformat(),
            "close_time": self.close_time.isoformat(),
            "timeframe_seconds": self.timeframe_seconds,
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": None if self.volume is None else str(self.volume),
        }


@dataclass(frozen=True)
class Quote:
    chain: str
    token_in: str
    token_out: str
    side: Side
    amount_in: Decimal
    amount_out: Decimal
    price: Decimal
    price_impact_pct: Decimal | None
    fee_usd: Decimal
    kind: QuoteKind
    route: str | None
    source: SourceRef
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain": self.chain,
            "token_in": self.token_in,
            "token_out": self.token_out,
            "side": self.side,
            "amount_in": str(self.amount_in),
            "amount_out": str(self.amount_out),
            "price": str(self.price),
            "price_impact_pct": None
            if self.price_impact_pct is None
            else str(self.price_impact_pct),
            "fee_usd": str(self.fee_usd),
            "kind": self.kind,
            "route": self.route,
            "source": self.source.to_dict(),
        }


# Human-readable text for each risk reason code. The code stays the stable identifier;
# the message is for display and may change between releases.
RISK_REASON_MESSAGES: dict[str, str] = {
    "INVALID_SIDE": "Order side must be buy or sell.",
    "INVALID_AMOUNT": "Order amount must be greater than zero.",
    "ORDER_LIMIT": "Order size exceeds the configured limit.",
    "SIZE_BELOW_MIN": "Order size is below the configured minimum.",
    "SIZE_ABOVE_MAX": "Order size is above the configured maximum.",
    "EQUITY_UNKNOWN": "Account equity is unknown; cannot size against it.",
    "PROFILE_UNKNOWN": "The requested risk profile is not allowed for this account.",
    "PAPER_MARK_LIMIT": "Order size exceeds the paper-mark limit.",
    "PRICE_IMPACT_TOO_HIGH": "Price impact exceeds the configured ceiling.",
    "QUOTE_STALE": "Quote is too old to trust.",
    "MARK_TIMESTAMP_FUTURE": "Market observation is dated ahead of the clock.",
    "PRICE_UNKNOWN": "Token price is unknown.",
    "LIQUIDITY_UNKNOWN": "Token liquidity is unknown.",
    "LIQUIDITY_TOO_LOW": "Token liquidity is below the minimum.",
    "NO_PAIR": "No market pair is available for the token.",
    "SNAPSHOT_STALE": "Market snapshot is stale.",
    "PAIR_AGE_UNKNOWN": "Pair creation time is unknown.",
    "PAIR_TOO_YOUNG": "Pair is younger than the minimum age.",
    "EXIT_NO_ROUTE": "No executable exit route is available.",
    "NO_SELL_ROUTE": "No sell route is available.",
    "NO_ROUTE": "No route is available.",
    "POSITION_LIMIT": "Position size would exceed the configured limit.",
    "MAX_OPEN_POSITIONS": "Maximum number of open positions reached.",
    "DAILY_LOSS_LIMIT": "Daily loss limit reached.",
    "INSUFFICIENT_CASH": "Insufficient cash to cover the order.",
    "SHORTFALL_FUNDED": "Shortfall was funded.",
    "INSUFFICIENT_POSITION": "Insufficient position to sell.",
    "POSITION_CHANGED": "Position changed while the order was being priced.",
    "KILL_SWITCH_ACTIVE": "Kill switch is active.",
    "KILL_SWITCH_ARMED": "Kill switch is armed; new risk-increasing orders are refused.",
    "WALLET_UNKNOWN": "No paper wallet settles this chain.",
    "SETTLEMENT_ASSET_UNKNOWN": "The paper wallet does not settle in the ledger asset.",
}


class RiskReason(str):
    """One structured reason a risk decision (or order) was rejected.

    Subclasses ``str`` so the code remains usable wherever a reason code string was
    expected: tuple equality, membership tests and string joining all keep working.
    """

    def __new__(
        cls,
        code: str,
        message: str | None = None,
        detail: Any = None,
    ) -> RiskReason:
        return super().__new__(cls, code)

    def __init__(
        self,
        code: str,
        message: str | None = None,
        detail: Any = None,
    ) -> None:
        self.code = code
        self.message = message if message is not None else RISK_REASON_MESSAGES.get(code, code)
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "detail": _json_safe(self.detail),
        }


def _json_safe(value: Any) -> Any:
    from decimal import Decimal

    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reasons: tuple[RiskReason, ...]
    max_order_usd: Decimal | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reasons": list(self.reasons),
            "max_order_usd": None if self.max_order_usd is None else str(self.max_order_usd),
        }


@dataclass(frozen=True)
class OrderRequest:
    chain: str
    token_address: str
    side: Side
    amount_usd: Decimal
    idempotency_key: str
    strategy_id: str | None = None
    # The exact token quantity for a sell. A sell is always bounded to what is held; a
    # caller that knows the position (a protection arm, a position screen) sizes it
    # directly instead of converting a USD notional through a mark. Buys size in USD.
    quantity: Decimal | None = None
    # A sell that exists to reduce an open position, never to open or extend risk. The
    # order service sets this only after it has bounded the quantity to what is held;
    # risk then knows the intent is an exit and does not apply entry-only gates.
    reduce_only: bool = False
    # Optional requested trade risk profile. The server allows it only when it matches
    # the account's configured profile.
    risk_profile: str | None = None


@dataclass(frozen=True)
class PositionState:
    chain: str
    token_address: str
    symbol: str
    quantity: Decimal
    cost_usd: Decimal

    @property
    def avg_cost_usd(self) -> Decimal:
        return Decimal(0) if self.quantity == 0 else self.cost_usd / self.quantity


MarkStatus = Literal["fresh", "stale", "unknown", "future"]


@dataclass(frozen=True)
class PositionView:
    """One open position as the server computed it.

    ``mark_status`` and ``mark_age_seconds`` are the server's own freshness verdict for the
    mark, so a client never re-derives staleness from a timestamp and a threshold of its own:
    the age is null when there is no usable observation, and the status names why
    (``unknown`` with no mark, ``future`` for an observation dated ahead of the clock).
    """

    chain: str
    token_address: str
    symbol: str
    quantity: Decimal
    avg_cost_usd: Decimal
    mark_usd: Decimal | None
    market_value_usd: Decimal | None
    unrealized_usd: Decimal | None
    realized_usd: Decimal
    source: SourceRef | None
    mark_status: MarkStatus = "unknown"
    mark_age_seconds: int | None = None
    mode: Mode = "paper"

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain": self.chain,
            "token_address": self.token_address,
            "symbol": self.symbol,
            "quantity": str(self.quantity),
            "avg_cost_usd": str(self.avg_cost_usd),
            "mark_usd": None if self.mark_usd is None else str(self.mark_usd),
            "market_value_usd": None
            if self.market_value_usd is None
            else str(self.market_value_usd),
            "unrealized_usd": None if self.unrealized_usd is None else str(self.unrealized_usd),
            "realized_usd": str(self.realized_usd),
            "source": None if self.source is None else self.source.to_dict(),
            "mark_status": self.mark_status,
            "mark_age_seconds": self.mark_age_seconds,
            "mode": self.mode,
        }
