"""Shared domain types for the ezCHAIN paper slice.

Money is a decimal USD string at the API boundary; prices, fees and token quantities use
Decimal inside the domain. No floats cross a money path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

Mode = Literal["paper", "live"]
Side = Literal["buy", "sell"]
QuoteKind = Literal["jupiter", "paper_mark"]
StrategyKind = Literal["momentum", "dip", "dca", "threshold", "graph"]
StrategyStatus = Literal["active", "paused"]


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class SourceRef:
    name: str
    url: str | None
    observed_at: datetime

    def age_seconds(self, now: datetime | None = None) -> float:
        observed = (
            self.observed_at.astimezone(UTC)
            if self.observed_at.tzinfo is not None
            else self.observed_at.replace(tzinfo=UTC)
        )
        return max(0.0, ((now or utcnow()) - observed).total_seconds())

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


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reasons: tuple[str, ...]
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


@dataclass(frozen=True)
class PositionView:
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
            "mode": self.mode,
        }
