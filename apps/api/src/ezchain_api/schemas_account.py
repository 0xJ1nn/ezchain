"""Read-only account, performance and operator schemas.

Money and prices are decimal strings computed on the server; the UI formats only
(CONTRACT I10). An undefined number is ``null``, never zero (CONTRACT I3).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .schemas import KillSwitchOut

Mode = Literal["paper"]


class LedgerEntryOut(BaseModel):
    """One append-only journal entry, exactly as persisted."""

    id: str
    group_id: str
    account: str
    asset: str
    amount: str
    usd_value: str
    kind: str
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
    by_token: list[TokenPerformanceOut]
    daily_realized: list[DailyRealizedOut]


class AttributionRowOut(TokenPerformanceOut):
    """A token row plus its share of absolute realized PnL across tokens."""

    share_pct: str | None = None


class PaperLimitsOut(BaseModel):
    """Risk limits the UI may show. Never contains a token, database URL or secret."""

    mode: Mode = "paper"
    max_order_usd: str
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
