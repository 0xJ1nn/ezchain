"""Read-only account, performance and operator schemas.

Money and prices are decimal strings computed on the server; the UI formats only
(CONTRACT I10). An undefined number is ``null``, never zero (CONTRACT I3).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from .schemas import KillSwitchOut

Mode = Literal["paper"]


class LedgerEntryOut(BaseModel):
    """One append-only journal entry, exactly as persisted.

    ``wallet_id`` names the paper wallet a cash leg settled to when it was written. It is
    null on every leg that is not cash (position, the `fees:USD` expense, PnL and equity
    legs). The no-risk funding top-up *is* cash and carries the settlement wallet id
    because it is booked as settlement capital; its ``funding:USD`` contra leg carries
    none. A null is never given a wallet at read time: on a legacy cash row it means the
    row predates wallet attribution, not that it settled nowhere.

    ``actor_kind`` and ``actor_id`` name the principal that placed the order this group
    belongs to, copied onto each leg when it was written. A row written before the actor
    was recorded reports ``unknown`` with a null id; that is never derived afterwards.

    ``bucket_id`` names the agent bucket a cash leg was drawn from or returned to. It is
    null on a non-cash leg, a person's order, and every row that predates buckets. The
    bucket's deployment is a replay of the rows that name it, never a stored balance.
    """

    id: str
    seq: int
    group_id: str
    account: str
    asset: str
    amount: str
    usd_value: str
    kind: str
    wallet_id: str | None = None
    bucket_id: str | None = None
    actor_kind: str = "unknown"
    actor_id: str | None = None
    created_at: str


class TokenPerformanceOut(BaseModel):
    """Per-token projection from ledger groups and the latest known mark.

    ``realized_usd`` is net of fees, because the ledger books each fee as its own leg.
    ``fees_usd`` is shown separately for transparency and is not additive to it.
    """

    chain: str
    token_address: str
    symbol: str
    realized_usd: str
    unrealized_usd: str | None = None
    fees_usd: str
    trades: int
    quantity: str
    avg_cost_usd: str | None = None
    mark_usd: str | None = None
    market_value_usd: str | None = None


class DailyRealizedOut(BaseModel):
    """One UTC day of closed and fee-bearing ledger groups.

    ``realized_usd`` is already net of fees, so ``fees_usd`` is disclosure, not an
    additional subtraction.
    """

    date: str
    realized_usd: str
    fees_usd: str


class PaperPerformanceOut(BaseModel):
    """PAPER performance from the append-only ledger.

    ``trades`` counts fills (executions). ``wins``/``losses`` count closed sell groups
    with a positive/negative net result; a breakeven close is in neither, so
    ``win_rate_pct`` is null when no close has a direction.
    """

    mode: Mode = "paper"
    as_of: str
    cash_usd: str
    # Null when an open position has no known mark: equity and unrealized cannot be
    # known without inventing a price.
    equity_usd: str | None = None
    realized_usd: str
    unrealized_usd: str | None = None
    fees_usd: str
    trades: int
    wins: int
    losses: int
    win_rate_pct: str | None = None
    open_positions: int
    complete: bool = False
    marks_unknown: bool = False
    by_token: list[TokenPerformanceOut]
    daily_realized: list[DailyRealizedOut]
    window: str = "all"
    source: str = "ledger"
    reason: str | None = None
    equity_series: list[dict[str, Any]] = []
    realized_pnl_series: list[dict[str, Any]] = []
    drawdown_series: list[dict[str, Any]] = []
    series: dict[str, Any] = {}


class AttributionRowOut(TokenPerformanceOut):
    """A token row plus its share of absolute realized PnL across tokens."""

    share_pct: str | None = None


class PaperLimitsOut(BaseModel):
    """Risk limits the UI may show. Never contains a token, database URL or secret."""

    mode: Mode = "paper"
    risk_profile: str = "mid"
    trade_risk_profile: str = "mid"
    min_order_usd: str | None = None
    max_order_usd: str
    max_trade_usd: str | None = None
    max_order_pct_of_equity: str | None = None
    per_token_cap_usd: str | None = None
    max_slippage_bps: int | None = None
    max_position_usd: str
    max_open_positions: int
    max_daily_loss_usd: str
    min_pair_age_minutes: int
    min_liquidity_usd: str
    paper_mark_max_order_usd: str
    paper_fee_bps: int
    max_quote_age_seconds: int
    kill_switch: KillSwitchOut


class AlertOut(BaseModel):
    id: str
    code: str
    severity: Literal["critical", "warning", "info"]
    title: str
    detail: str
    created_at: str
    resource: str


class PaperAlertsOut(BaseModel):
    as_of: str
    alerts: list[AlertOut]
