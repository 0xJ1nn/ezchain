"""API schemas. Money is returned as decimal strings; the web formats only."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool = False
    detail: str | None = None


class CapabilitiesOut(BaseModel):
    browse: bool
    paper_execute: bool
    live_execute: bool
    quote_kind: str


class ChainOut(BaseModel):
    id: str
    name: str
    family: str
    native_asset: str
    tier: str
    capabilities: CapabilitiesOut
    venues: list[str]
    notes: str = ""


class SourceOut(BaseModel):
    name: str
    url: str | None = None
    observed_at: str


class MarketRowOut(BaseModel):
    chain: str
    pair_address: str
    dex: str
    base_token: str
    quote_token: str
    symbol: str
    name: str
    price_usd: str
    liquidity_usd: str
    volume_24h_usd: str
    price_change_m5: str | None = None
    price_change_h1: str | None = None
    price_change_h6: str | None = None
    price_change_h24: str | None = None
    pair_created_at: str | None = None
    url: str | None = None
    source: SourceOut


class MarketListOut(BaseModel):
    rows: list[MarketRowOut]
    source: SourceOut
    stale: bool


class PairOut(BaseModel):
    pair_address: str
    dex: str
    quote_token: str
    price_usd: str
    liquidity_usd: str
    volume_24h_usd: str
    url: str | None = None


class TokenOut(BaseModel):
    chain: str
    address: str
    symbol: str
    name: str
    price_usd: str | None = None
    liquidity_usd: str | None = None
    volume_24h_usd: str | None = None
    price_change_m5: str | None = None
    price_change_h1: str | None = None
    price_change_h6: str | None = None
    price_change_h24: str | None = None
    pairs: list[PairOut]
    safety: dict[str, Any]
    source: SourceOut | None = None
    stale: bool


class QuoteOut(BaseModel):
    chain: str
    token_in: str
    token_out: str
    side: str
    amount_in: str
    amount_out: str
    price: str
    price_impact_pct: str | None = None
    fee_usd: str
    kind: str
    route: str | None = None
    source: SourceOut


class PaperOrderIn(BaseModel):
    chain: str = Field(min_length=2, max_length=40)
    token_address: str = Field(min_length=8, max_length=160)
    side: Literal["buy", "sell"]
    amount_usd: Decimal = Field(gt=0)
    idempotency_key: str = Field(min_length=8, max_length=120)
    strategy_id: str | None = None

    @field_validator("amount_usd")
    @classmethod
    def cents(cls, value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"))


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    chain: str
    token_address: str
    side: str
    amount_usd: str
    status: str
    reason: str | None = None
    quote_kind: str | None = None
    created_at: str
    filled_at: str | None = None


class FillOut(BaseModel):
    id: str
    order_id: str
    chain: str
    token_address: str
    side: str
    quantity: str
    price_usd: str
    notional_usd: str
    fee_usd: str
    quote_kind: str
    created_at: str


class PositionOut(BaseModel):
    chain: str
    token_address: str
    symbol: str
    quantity: str
    avg_cost_usd: str
    mark_usd: str | None = None
    market_value_usd: str | None = None
    unrealized_usd: str | None = None
    realized_usd: str
    source: SourceOut | None = None
    mode: str = "paper"


class PaperAccountOut(BaseModel):
    cash_usd: str
    equity_usd: str
    realized_usd: str
    unrealized_usd: str
    positions: list[PositionOut]
    mode: str = "paper"


class ReconciliationCheckOut(BaseModel):
    id: str
    ok: bool
    detail: str


class ReconciliationTotalsOut(BaseModel):
    cash_usd: str
    replay_cash_usd: str
    open_positions: int
    fills: int
    filled_orders: int
    orphan_fills: int
    filled_orders_without_single_fill: int
    negative_cash: int
    negative_positions: int
    quote_kinds: dict[str, int]


class ReconciliationOut(BaseModel):
    ok: bool
    checks: list[ReconciliationCheckOut]
    as_of: str
    totals: ReconciliationTotalsOut


class KillSwitchOut(BaseModel):
    active: bool
    reason: str | None = None
    updated_at: str | None = None
    scope: Literal["strategy"] = "strategy"


class KillSwitchIn(BaseModel):
    active: bool
    reason: str | None = Field(default=None, max_length=200)


class StrategyIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    chain: str = Field(min_length=2, max_length=40)
    token_address: str = Field(min_length=8, max_length=160)
    token_symbol: str = Field(min_length=1, max_length=40)
    kind: Literal["momentum", "dip", "dca", "threshold"]
    params: dict[str, Any] = Field(default_factory=dict)
    status: Literal["active", "paused"] = "active"


class StrategyPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    params: dict[str, Any] | None = None
    status: Literal["active", "paused"] | None = None


class StrategyOut(BaseModel):
    id: str
    name: str
    chain: str
    token_address: str
    token_symbol: str
    kind: str
    params: dict[str, Any]
    status: str
    last_run_at: str | None = None
    created_at: str


class StrategyRunOut(BaseModel):
    id: str
    strategy_id: str
    action: str
    reason: str
    inputs: dict[str, Any]
    order_id: str | None = None
    created_at: str
