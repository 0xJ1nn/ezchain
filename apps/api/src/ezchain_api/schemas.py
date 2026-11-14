"""API schemas. Money is returned as decimal strings; the web formats only."""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .services.system_flags import KILL_SWITCH_SCOPES


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
    """One live pair row with the contract's own source and read-time age.

    ``age_seconds`` and ``stale`` come from the row's ``SourceRef`` against the read
    clock, not from a freshness flag the adapter wrote.
    """

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
    age_seconds: int | None = None
    stale: bool | None = None


MarketSort = Literal["trending", "liquidity", "new", "change_h24", "pair_age"]
MarketSortSemantics = Literal["global_sort", "per_chain_sort", "per_chain_round_robin"]


class MarketCoverageOut(BaseModel):
    """One chain's contribution to a discovery pass; counts are pre-slice.

    ``no_seeds`` means no address existed for the chain, so the pass did not ask the
    provider and its population is unknown — never an empty observation.
    """

    chain: str
    requested: int
    returned: int
    reason: Literal["pairs", "no_pairs", "no_seeds", "error", "timeout"]


class MarketFiltersOut(BaseModel):
    """The validated filters actually applied to this request.

    ``unknown_excluded`` names every active filter that dropped a row because the
    provider value was unknown. Unknown is never treated as zero.
    """

    chain: str
    q: str | None = None
    dex: str | None = None
    min_liquidity_usd: str | None = None
    min_volume_24h_usd: str | None = None
    min_change_h24: str | None = None
    max_change_h24: str | None = None
    min_pair_age_minutes: int | None = None
    max_pair_age_minutes: int | None = None
    unknown_excluded: dict[str, int] = Field(default_factory=dict)


class MarketPopulationOut(BaseModel):
    """What the discovery pass saw before this page was sliced from it."""

    chains_scanned: int | None = None
    addresses_requested: int | None = None
    rows_discovered: int
    rows_after_filters: int
    rows_returned: int
    truncated: bool = False
    sort: MarketSort = "trending"
    sort_semantics: MarketSortSemantics = "global_sort"


class MarketListOut(BaseModel):
    rows: list[MarketRowOut]
    source: SourceOut
    stale: bool
    coverage: list[MarketCoverageOut] | None = None
    ordered_by: Literal["chain_round_robin", "per_chain_sort", "global_sort"] = "global_sort"
    sort: MarketSort = "trending"
    sort_semantics: MarketSortSemantics = "global_sort"
    offset: int = 0
    limit: int = 50
    total: int = 0
    has_more: bool = False
    next_offset: int | None = None
    next_cursor: str | None = None
    truncated: bool = False
    applied_filters: MarketFiltersOut | None = None
    population: MarketPopulationOut | None = None


class StoredMarketListOut(BaseModel):
    """One page of persisted observations with the same paging contract as live rows."""

    rows: list[StoredSnapshotOut]
    as_of: str
    offset: int = 0
    limit: int = 50
    total: int = 0
    has_more: bool = False
    next_offset: int | None = None
    next_cursor: str | None = None
    applied_filters: MarketFiltersOut | None = None


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
    """A persisted token observation with the contract's source and read-time age.

    ``source`` is null when the row carries no usable provenance (a blank name or the
    writer's ``unknown`` sentinel), and then ``age_seconds`` and ``stale`` are null too:
    there is no observation to age, and an unprovenanced row is never reported as fresh.
    """

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
    source: SourceOut | None = None
    observed_at: str
    age_seconds: int | None = None
    stale: bool | None = None
    pair_address: str | None = None
    dex: str | None = None
    base_token_address: str | None = None
    quote_token_address: str | None = None
    pair_created_at: str | None = None
    url: str | None = None
    pair_age_seconds: int | None = None


class PairOut(BaseModel):
    pair_address: str
    dex: str
    quote_token: str
    price_usd: str
    liquidity_usd: str | None = None
    volume_24h_usd: str | None = None
    url: str | None = None


class TokenOut(BaseModel):
    """Token header whose source and age come from the contract, not the adapter.

    ``age_seconds`` is measured at read time from ``source``. ``stale`` is the contract's
    verdict at the same clock; when there is no source the age is null and ``stale`` is
    ``False`` because the token page treats a missing observation as unknown, not stale.
    """

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
    age_seconds: int | None = None
    pair_address: str | None = None
    dex: str | None = None
    base_token_address: str | None = None
    quote_token_address: str | None = None
    pair_created_at: str | None = None
    url: str | None = None
    pair_age_seconds: int | None = None


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


class CostPreviewOut(BaseModel):
    """The pre-trade cost sheet. Nulls are unknowns ("—"), never zeros."""

    platform_fee_usd: str
    venue_fee_usd: str
    network_fee_usd: str | None = None
    price_impact_pct: str | None = None
    slippage_bps: int | None = None
    worst_case_out: str | None = None
    fill_fee_usd: str
    source: SourceOut


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
    # Same sheet the journal records on the intent; the preview and the journal agree.
    cost_preview: CostPreviewOut


class PaperOrderIn(BaseModel):
    """A manual paper order.

    Deliberately has no ``strategy_id``: strategy attribution is internal to the engine,
    which reaches the order service through ``KillSwitchExecutor``. It also has no
    ``actor``: the placing principal is resolved from the authenticated caller and a body
    that tries to supply it is refused. Unknown fields are refused (``422``, with the
    field named in ``loc``), so a hand-written request can neither claim strategy or
    principal attribution nor have a misspelt limit silently dropped. The refusal happens
    before the quote or any journal write.
    """

    model_config = ConfigDict(extra="forbid")

    chain: str = Field(min_length=2, max_length=40)
    token_address: str = Field(min_length=8, max_length=160)
    side: Literal["buy", "sell"]
    amount_usd: Decimal = Field(gt=0)
    idempotency_key: str = Field(min_length=8, max_length=120)
    # The exact token quantity of a sell. The service bounds it to the open position.
    quantity: Decimal | None = Field(default=None, gt=0)
    # A sell that exists to reduce the open position. Entry-only risk gates do not apply;
    # the position bound, quote freshness and an executable route still do.
    reduce_only: bool = False
    # Optional requested risk profile. Any defined profile is accepted (the owner's own
    # risk choice); the deployment profile is the default. An unknown name is a typed
    # rejection.
    risk_profile: str | None = None
    trade_risk_profile: str | None = None
    profile: str | None = None

    @model_validator(mode="after")
    def sell_only_fields(self) -> PaperOrderIn:
        if self.side == "buy" and (self.reduce_only or self.quantity is not None):
            raise ValueError("reduce_only and quantity are only valid for a sell")
        return self

    @field_validator("quantity", mode="before")
    @classmethod
    def finite_quantity(cls, value: object) -> object:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("quantity must be finite")
        if isinstance(value, Decimal) and not value.is_finite():
            raise ValueError("quantity must be finite")
        return value

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


class RiskReasonOut(BaseModel):
    code: str
    message: str
    detail: Any = None


class ActorOut(BaseModel):
    """Who placed an order, as the server recorded it.

    ``kind`` is ``human`` (the deployment operator), ``agent`` (a scoped key), ``strategy``
    (the engine) or ``unknown``. ``id`` addresses that kind: the operator subject, the agent
    key id, or the strategy id. It is null exactly when the kind is unknown, which is what a
    row written before the actor was recorded reports. A client can never supply this.
    """

    kind: str
    id: str | None = None


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    chain: str
    token_address: str
    side: str
    amount_usd: str
    status: str
    reason: str | None = None
    reasons: list[RiskReasonOut] = Field(default_factory=list)
    quote_kind: str | None = None
    actor: ActorOut = Field(default_factory=lambda: ActorOut(kind="unknown"))
    # The approval this order waits in or was decided by. Null for a person's own order,
    # an engine order, and an agent order under the ``full_auto`` tier.
    approval_id: str | None = None
    approval_status: str | None = None
    created_at: str
    filled_at: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _derive_reasons(cls, data: Any) -> Any:
        if isinstance(data, dict):
            reason = data.get("reason")
            reasons = data.get("reasons")
            if not reasons and reason is not None:
                from ezchain_core.reason_codes import RISK_REASON_MESSAGES

                data = dict(data)
                data["reasons"] = [
                    {
                        "code": reason,
                        "message": RISK_REASON_MESSAGES.get(reason, reason),
                        "detail": None,
                    }
                ]
            elif reasons is None:
                data = dict(data)
                data["reasons"] = []
        return data


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


class ApprovalIntentOut(BaseModel):
    """The stored order request a person is approving, exactly as the agent proposed it."""

    model_config = ConfigDict(extra="allow")

    chain: str
    token_address: str
    side: str
    amount_usd: str
    idempotency_key: str
    strategy_id: str | None = None
    quantity: str | None = None
    reduce_only: bool = False
    risk_profile: str | None = None


class ApprovalDecisionOut(BaseModel):
    """The server's own risk evidence for an approval. Never text from the agent."""

    model_config = ConfigDict(extra="allow")

    stage: str | None = None
    allowed: bool | None = None
    reasons: list[RiskReasonOut] = Field(default_factory=list)


class ApprovalOut(BaseModel):
    """One agent order waiting for a person, and the record of what was decided.

    ``status`` is one declared lifecycle value and ``next_action`` names what a reader may
    do in it (``approve_or_reject`` while pending, ``execute`` for the one claim an
    approval takes, ``none`` once terminal). ``decided_by`` is the person; a null means no
    person has decided yet.
    """

    id: str
    order_id: str
    status: Literal[
        "pending", "approved", "rejected", "expired", "superseded", "executed", "failed"
    ]
    next_action: Literal["approve_or_reject", "execute", "none"]
    intent: ApprovalIntentOut
    risk_decision: ApprovalDecisionOut
    execution_reason: str | None = None
    decided_by: ActorOut | None = None
    decision_note: str | None = None
    created_at: str
    expires_at: str | None = None
    decided_at: str | None = None
    executed_at: str | None = None
    mode: str = "paper"


class ApprovalDecisionIn(BaseModel):
    """A person's decision. It carries a note, never an order: the stored intent executes."""

    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=1000)


class ApprovalRejectIn(BaseModel):
    """A rejection requires the person's reason."""

    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=1, max_length=1000)


class AutomationTierIn(BaseModel):
    """Switch a user's automation tier. ``full_auto`` requires the acknowledgement."""

    model_config = ConfigDict(extra="forbid")

    tier: Literal["manual", "full_auto"]
    acknowledge_risk: bool = False

    @model_validator(mode="after")
    def full_auto_needs_acknowledgement(self) -> AutomationTierIn:
        if self.tier == "full_auto" and not self.acknowledge_risk:
            raise ValueError("switching to full_auto requires acknowledge_risk")
        return self


class AutomationDisclosureOut(BaseModel):
    version: str
    text: str
    acknowledged_at: str | None = None


class AutomationOut(BaseModel):
    """The per-user automation tier and the disclosure behind it.

    ``agent_orders`` is the plain-language consequence: ``wait_for_a_person`` under
    ``manual``, ``execute_automatically`` under ``full_auto``.
    """

    user_id: str
    tier: Literal["manual", "full_auto"]
    tiers: list[Literal["manual", "full_auto"]]
    updated_at: str | None = None
    updated_by: ActorOut | None = None
    disclosure: AutomationDisclosureOut
    agent_orders: Literal["wait_for_a_person", "execute_automatically"]


class PositionOut(BaseModel):
    """One open position, with the server's own verdict on its mark's freshness.

    ``mark_age_seconds`` is null when there is no usable observation, and ``mark_status``
    says why, so no client has to re-derive staleness from a timestamp.
    """

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
    mark_status: Literal["fresh", "stale", "unknown", "future"] = "unknown"
    mark_age_seconds: int | None = None
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
    mark_status: Literal["fresh", "stale", "unknown", "future"]


class PaperWalletOut(BaseModel):
    """A paper wallet, or the unallocated cash bucket, and its ledger-derived cash.

    The row stores no balance: ``balance_usd`` is the sum of the cash legs attributed to
    it. ``mode`` travels with the number so a wallet read can never be mistaken for a live
    balance. ``id`` is null only for the ``unallocated`` row, which keeps cash whose
    attributed wallet id no longer has a row; it is a bucket, not a wallet, and it exists
    so the list is a complete partition of ``cash_usd``.
    """

    id: str | None = None
    chain: str
    asset: str
    label: str
    balance_usd: str
    mode: str = "paper"


class PaperWalletCompositionOut(BaseModel):
    """One wallet's contribution to equity: settled cash and its chain's positions.

    ``market_value_usd`` is null when any position on the wallet's chain is unpriced; the
    chain is never valued at zero. ``settled_usd`` is always known because it is a ledger
    sum. ``wallet_id`` is null only for the ``unallocated`` row that keeps positions on a
    chain with no wallet from disappearing out of the total.
    """

    wallet_id: str | None = None
    chain: str
    asset: str
    label: str
    settled_usd: str
    positions_usd: str | None = None
    market_value_usd: str | None = None
    share_pct: str | None = None
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
    mark_status: Literal["fresh", "stale", "unknown", "future"]


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
    wallets: list[PaperWalletOut]
    by_wallet: list[PaperWalletCompositionOut]
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
    """One flag, two levels: global applies to every caller, strategy to strategy intents."""

    active: bool
    reason: str | None = None
    updated_at: str | None = None
    scope: Literal["global"] = "global"
    scopes: list[Literal["global", "strategy"]] = Field(
        default_factory=lambda: list(KILL_SWITCH_SCOPES)
    )


class KillSwitchIn(BaseModel):
    """One switch, one step, in either direction.

    Arming and releasing are symmetric: both accept an optional reason and both store it
    when supplied. The reason is operator bookkeeping, not a protection for a key or an
    outsider, so requiring it on release alone gated the owner's own trading.
    """

    model_config = ConfigDict(extra="forbid")

    active: bool
    reason: str | None = Field(default=None, max_length=200)


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
    data_mode: str | None = None
    metrics: dict[str, Any]
    equity: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    node_hits: dict[str, int]
    final_cash_usd: str | None = None
    final_quantity: str | None = None
    final_equity_usd: str | None = None
    warmup_bars: dict[str, int] | None = None


class ProtectionArmIn(BaseModel):
    chain: str = Field(min_length=2, max_length=40)
    token_address: str = Field(min_length=8, max_length=160)
    token_symbol: str = Field(min_length=1, max_length=40)
    kind: Literal["stop_loss", "trailing_stop", "take_profit"]
    params: dict[str, Any] = Field(default_factory=dict)
    profile_id: str | None = Field(default=None, min_length=1, max_length=36)
    expires_at: str | None = None


class ProtectionArmPatch(BaseModel):
    params: dict[str, Any]
    expected_version: int | None = Field(default=None, ge=1)


class ProtectionArmCancelIn(BaseModel):
    expected_version: int | None = Field(default=None, ge=1)


class ProtectionArmOut(BaseModel):
    id: str
    chain: str
    token_address: str
    token_symbol: str
    kind: str
    status: str
    params: dict[str, Any]
    peak_usd: str | None = None
    triggered_levels: list[int] = Field(default_factory=list)
    triggered_at: str | None = None
    order_id: str | None = None
    expires_at: str | None = None
    profile_id: str | None = None
    version: int = 1
    last_evaluation: dict[str, Any] | None = None
    created_at: str
    updated_at: str
    mode: str = "paper"


class ProtectionProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    chain: str | None = Field(default=None, min_length=2, max_length=40)
    kind: Literal["manual", "smart"]
    params: dict[str, Any] = Field(default_factory=dict)
    rules: list[str] = Field(default_factory=list)


class ProtectionProfileOut(BaseModel):
    id: str
    name: str
    chain: str | None = None
    kind: str
    stop_loss_pct: str | None = None
    trailing_pct: str | None = None
    take_profit_pct: str | None = None
    rules: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    mode: str = "paper"


class ProtectionEvaluateOut(BaseModel):
    arm: ProtectionArmOut
    action: str
    reason: str
    sell_fraction: str
    order_id: str | None = None


class ProtectionEffectiveOut(BaseModel):
    chain: str
    profile: ProtectionProfileOut | None = None


class ProtectionEventOut(BaseModel):
    id: str
    arm_id: str
    seq: int
    action: str
    reason: str | None = None
    rule_snapshot: dict[str, Any] | None = None
    mark: str | None = None
    observed_at: str
    order_id: str | None = None
    created_at: str | None = None


SignalWindow = Literal["h1", "h24"]
SignalDirection = Literal["up", "down"]


class SignalMoverOut(BaseModel):
    """One ranked mover. The change is the window's own price change, as a decimal string."""

    rank: int
    chain: str
    token_address: str
    pair_address: str
    dex: str
    symbol: str
    name: str
    price_usd: str
    liquidity_usd: str
    volume_24h_usd: str | None = None
    change_pct: str
    source: SourceOut
    observed_at: str
    age_seconds: int


class SignalExclusionsOut(BaseModel):
    """Why rows did not reach the table.

    Every dropped row is counted under the first reason that applies to it. Unknown is
    its own bucket: an unknown value is never counted as a zero that failed a filter.
    """

    unknown_token: int = 0
    duplicate_token: int = 0
    stale: int = 0
    unknown_change: int = 0
    unknown_liquidity: int = 0
    below_liquidity_floor: int = 0
    wrong_direction: int = 0


class SignalAppliedOut(BaseModel):
    """The filters and the bounds this response was produced under."""

    chain: str
    window: SignalWindow
    direction: SignalDirection
    min_liquidity_usd: str
    max_age_seconds: int
    candidate_rows: int
    candidate_sort: str
    limit: int


class SignalMoversOut(BaseModel):
    """The movers read model for one window and one direction.

    An empty ``rows`` list carries a ``reason`` code. No mover is invented to fill it.
    """

    window: SignalWindow
    direction: SignalDirection
    as_of: str
    rows: list[SignalMoverOut]
    total: int
    limit: int
    truncated: bool = False
    reason: str | None = None
    rows_read: int = 0
    exclusions: SignalExclusionsOut = Field(default_factory=SignalExclusionsOut)
    applied: SignalAppliedOut
    source: SourceOut
    stale: bool = False


class StrategyPerformanceOut(BaseModel):
    """PAPER performance for one strategy, from the account ledger.

    ``realized_pnl_usd`` is net of fees and books a buy fee when the ledger pays it.
    ``market_value_usd`` is the strategy's contribution to account equity. An unknown
    mark leaves ``market_value_usd`` and ``unrealized_pnl_usd`` null and sets
    ``complete`` false; it is never valued at zero.
    """

    strategy_id: str
    label: Literal["PAPER"] = "PAPER"
    realized_pnl_usd: str
    unrealized_pnl_usd: str | None = None
    fees_usd: str
    trades: int
    wins: int
    losses: int
    win_rate_pct: str | None = None
    open_quantity: str | None = None
    avg_cost_usd: str | None = None
    mark_usd: str | None = None
    market_value_usd: str | None = None
    complete: bool = False
    marks_unknown: bool = False
    equity_curve: list[dict[str, str]]
    window: str = "all"
    source: str = "ledger"
    reason: str | None = None
    equity_series: list[dict[str, Any]] = []
    realized_pnl_series: list[dict[str, Any]] = []
    drawdown_series: list[dict[str, Any]] = []
    series: dict[str, Any] = {}
