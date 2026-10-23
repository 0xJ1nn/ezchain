"""API schemas. Money is returned as decimal strings; the web formats only."""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool = False
    detail: str | None = None


class CapabilitiesOut(BaseModel):
    browse: bool
    paper_execute: bool
    live_execute: bool
    # The primary provider a chain's badge names, plus the full fallback ladder.
    quote_kind: str
    quote_providers: list[str]
    executable_quotes: bool
    candles: bool


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
    liquidity_usd: str | None = None
    volume_24h_usd: str | None = None
    price_change_m5: str | None = None
    price_change_h1: str | None = None
    price_change_h6: str | None = None
    price_change_h24: str | None = None
    pair_created_at: str | None = None
    url: str | None = None
    source: SourceOut


class MarketCoverageOut(BaseModel):
    """One chain's contribution to a discovery pass; counts are pre-slice."""

    chain: str
    requested: int
    returned: int
    reason: Literal["pairs", "no_pairs", "error"]


class MarketListOut(BaseModel):
    rows: list[MarketRowOut]
    source: SourceOut
    stale: bool
    coverage: list[MarketCoverageOut] | None = None
    ordered_by: Literal["chain_round_robin", "global_sort"] = "global_sort"


class MarketChainOut(BaseModel):
    """One registered chain as the market monitor currently sees it."""

    id: str
    name: str
    family: str
    quote_kind: str
    status: Literal["ok", "stale", "no_data", "error", "scanning"]
    rows: int
    last_observed_at: str | None = None
    age_seconds: int | None = None
    last_attempt_at: str | None = None
    last_error: str | None = None


class MarketTotalsOut(BaseModel):
    chains: int
    covered: int
    stale: int
    no_data: int
    error: int
    snapshots: int


class MarketOverviewOut(BaseModel):
    as_of: str
    chains: list[MarketChainOut]
    totals: MarketTotalsOut


class StoredSnapshotOut(BaseModel):
    """A persisted token observation. Fields mirror the snapshot table; no pair/dex/url."""

    chain: str
    token_address: str
    symbol: str
    name: str
    price_usd: str | None = None
    liquidity_usd: str | None = None
    volume_24h_usd: str | None = None
    price_change_m5: str | None = None
    price_change_h1: str | None = None
    price_change_h6: str | None = None
    price_change_h24: str | None = None
    source: str
    observed_at: str
    age_seconds: int | None = None


class PairOut(BaseModel):
    pair_address: str
    dex: str
    quote_token: str
    price_usd: str
    liquidity_usd: str | None = None
    volume_24h_usd: str | None = None
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


class CandleOut(BaseModel):
    """One closed OHLCV bar. Prices are decimal strings; unknown volume is null."""

    open_time: str
    close_time: str
    timeframe_seconds: int
    open: str
    high: str
    low: str
    close: str
    volume: str | None = None


class CandlesOut(BaseModel):
    chain: str
    token_address: str
    timeframe: Literal["1m", "5m", "15m", "1h", "4h", "1d"]
    pool_address: str | None = None
    candles: list[CandleOut]
    source: SourceOut
    stale: bool
    reason: str | None = None


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
    """A manual paper order.

    Deliberately has no ``strategy_id``: strategy attribution is internal to the engine,
    which reaches the order service through ``KillSwitchExecutor``. An unknown field is
    ignored by Pydantic, so a hand-written request cannot claim strategy attribution.
    """

    chain: str = Field(min_length=2, max_length=40)
    token_address: str = Field(min_length=8, max_length=160)
    side: Literal["buy", "sell"]
    amount_usd: Decimal = Field(gt=0)
    idempotency_key: str = Field(min_length=8, max_length=120)

    @field_validator("amount_usd", mode="before")
    @classmethod
    def finite_before_parse(cls, value: object) -> object:
        """Reject `1e400` (a JSON float that becomes infinity) before Decimal parsing."""
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("amount_usd must be finite")
        if isinstance(value, Decimal) and not value.is_finite():
            raise ValueError("amount_usd must be finite")
        return value

    @field_validator("amount_usd")
    @classmethod
    def cents(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("amount_usd must be finite")
        try:
            quantized = value.quantize(Decimal("0.01"))
        except InvalidOperation as exc:
            raise ValueError("amount_usd is outside the supported range") from exc
        if quantized <= 0:
            raise ValueError("amount_usd must be > 0")
        return quantized

    @field_validator("idempotency_key")
    @classmethod
    def ascii_key(cls, value: str) -> str:
        """Keys cross process and log boundaries; non-ASCII/control bytes are refused."""
        if not value.isascii():
            raise ValueError("idempotency_key must be ASCII")
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("idempotency_key must not contain control characters")
        return value


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


class PaperUnpricedPositionOut(BaseModel):
    """An open position whose mark is unknown: it is never valued at zero."""

    chain: str
    token_address: str
    symbol: str
    quantity: str


class PaperAssetCompositionOut(BaseModel):
    """One asset's server-computed contribution to the paper account.

    ``share_pct`` is null whenever equity is incomplete or non-positive. Cash is the USD
    settlement row (``kind = "cash"``); its value is always known, but its share is still
    withheld while the equity denominator is unknown.
    """

    chain: str
    token_address: str | None = None
    symbol: str
    kind: Literal["position", "cash"]
    quantity: str
    mark_usd: str | None = None
    market_value_usd: str | None = None
    share_pct: str | None = None
    source: SourceOut | None = None
    mark_status: Literal["known", "unknown"]


class PaperChainCompositionOut(BaseModel):
    """One chain's server-computed total. Native quantity lives on the asset row.

    ``market_value_usd`` is null when any asset on the chain is unpriced: a partial chain
    total is never presented as the chain's value.
    """

    chain: str
    assets: int
    unpriced_assets: int = 0
    market_value_usd: str | None = None
    share_pct: str | None = None
    mark_status: Literal["known", "unknown"]


class PaperAccountOut(BaseModel):
    """The paper account read model. Equity is withheld when any open mark is unknown."""

    cash_usd: str
    equity_usd: str | None = None
    realized_usd: str
    unrealized_usd: str | None = None
    complete: bool
    marks_unknown: bool
    known_market_value_usd: str
    unpriced_positions: list[PaperUnpricedPositionOut]
    by_chain: list[PaperChainCompositionOut]
    by_asset: list[PaperAssetCompositionOut]
    positions: list[PositionOut]
    mode: str = "paper"


class ReconciliationCheckOut(BaseModel):
    id: str
    ok: bool
    detail: str


class ReconciliationTotalsOut(BaseModel):
    genesis_usd: str
    cash_usd: str
    fills_cash_usd: str
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

    @model_validator(mode="after")
    def release_needs_reason(self) -> KillSwitchIn:
        """Disarming is a deliberate act: it needs a recorded reason (RUNBOOK §10)."""
        if not self.active and not (self.reason or "").strip():
            raise ValueError("releasing the kill switch requires a reason")
        return self


class StrategyIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    chain: str = Field(min_length=2, max_length=40)
    token_address: str = Field(min_length=8, max_length=160)
    token_symbol: str = Field(min_length=1, max_length=40)
    kind: Literal["momentum", "dip", "dca", "threshold", "graph", "plugin"]
    params: dict[str, Any] = Field(default_factory=dict)
    plugin_id: str | None = Field(default=None, min_length=3, max_length=128)
    plugin_version: str | None = Field(default=None, max_length=32)
    status: Literal["active", "paused"] = "active"

    @model_validator(mode="after")
    def plugin_identity(self) -> StrategyIn:
        if self.kind == "plugin" and not self.plugin_id:
            raise ValueError("plugin_id is required when kind is plugin")
        if self.kind != "plugin" and (
            self.plugin_id is not None or self.plugin_version is not None
        ):
            raise ValueError("plugin_id/plugin_version are only valid when kind is plugin")
        return self


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
    plugin_id: str | None = None
    plugin_version: str | None = None
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


class GraphValidateIn(BaseModel):
    """The document is checked by the core validator, not by Pydantic here.

    ``Any`` is deliberate: a malformed document must produce the graph's own
    ``STRATEGY_GRAPH_INVALID`` envelope, not Pydantic's field-error list.
    """

    graph: Any = None


class GraphDryRunIn(BaseModel):
    graph: Any = None
    chain: str = Field(min_length=2, max_length=40)
    token_address: str = Field(min_length=8, max_length=160)
    max_position_usd: Decimal | None = Field(default=None, gt=0)


class BacktestIn(BaseModel):
    """A backtest request. Exactly one of ``graph`` / ``strategy_id`` names the graph."""

    graph: Any = None
    strategy_id: str | None = Field(default=None, min_length=1, max_length=36)
    chain: str | None = Field(default=None, min_length=2, max_length=40)
    token_address: str | None = Field(default=None, min_length=8, max_length=160)
    timeframe: Literal["1m", "5m", "15m", "1h", "4h", "1d"]
    bars: int = Field(ge=1, le=1000)
    fee_bps: int = Field(ge=0, le=10_000)
    slippage_bps: int = Field(ge=0, le=10_000)
    starting_cash_usd: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def one_graph_source(self) -> BacktestIn:
        if (self.graph is None) == (self.strategy_id is None):
            raise ValueError("provide exactly one of graph or strategy_id")
        return self


class BacktestOut(BaseModel):
    id: str
    created_at: str
    label: str
    request: dict[str, Any]
    data_source: str
    fill_model: str
    metrics: dict[str, Any]
    equity: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    node_hits: dict[str, int]
    final_cash_usd: str | None = None
    final_quantity: str | None = None
    final_equity_usd: str | None = None
    warmup_bars: dict[str, int] | None = None


class StrategyPerformanceOut(BaseModel):
    strategy_id: str
    label: Literal["PAPER"] = "PAPER"
    realized_pnl_usd: str
    unrealized_pnl_usd: str | None = None
    fees_usd: str
    trades: int
    wins: int
    losses: int
    win_rate_pct: str | None = None
    open_quantity: str
    avg_cost_usd: str | None = None
    mark_usd: str | None = None
    equity_curve: list[dict[str, str]]
