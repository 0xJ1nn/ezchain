"""Trade-policy schemas: the fields the order gate reads, served as they are enforced.

A page renders these rows and nothing else, so a policy field cannot appear in the UI
without being enforced by the order service (and a field the schema cannot express is not
added). Values are exact: decimals travel as strings, integers as numbers.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PolicyFieldOut(BaseModel):
    """One sizing field with its value, range and the description shown to the owner."""

    key: str
    label: str
    unit: str
    kind: Literal["decimal", "integer", "ratio"]
    description: str
    value: str | int
    minimum: str
    maximum: str


class PolicyProfileOut(BaseModel):
    """One named profile and every sizing field it bundles."""

    name: str
    label: str
    description: str
    fields: list[PolicyFieldOut]


class PolicyWarningOut(BaseModel):
    """A strategy setting that the order gate would refuse under its profile."""

    code: str
    message: str
    detail: dict[str, Any] | None = None


class StrategyPolicyOut(BaseModel):
    """One strategy's effective policy: its override or the deployment default.

    ``source`` names where the profile came from, never a guess. ``warnings`` lists the
    strategy's own sizing that the profile's resolver would reject, so a row cannot claim
    a size it cannot trade.
    """

    id: str
    name: str
    token_symbol: str
    risk_profile: str | None
    effective_profile: str | None
    source: Literal["strategy", "default"]
    order_usd: str | None = None
    max_position_usd: str | None = None
    warnings: list[PolicyWarningOut] = Field(default_factory=list)


class TradePolicyOut(BaseModel):
    """Every profile, the caller's active policy, and each strategy's effective policy."""

    mode: Literal["paper"] = "paper"
    active_profile: str | None
    profiles: list[PolicyProfileOut]
    fields: list[PolicyFieldOut]
    strategies: list[StrategyPolicyOut]


class StrategyRiskPolicyIn(BaseModel):
    """A partial per-strategy override. ``null`` returns the strategy to the default."""

    model_config = ConfigDict(extra="forbid")

    risk_profile: str | None = None
    acknowledge_risk: bool = False


class PreviewLimitsOut(BaseModel):
    min_order_usd: str
    max_order_usd: str
    max_order_pct_of_equity: str
    per_token_cap_usd: str
    max_slippage_bps: int


class TradePolicyPreviewOut(BaseModel):
    """The server's sizing verdict for one proposed buy under one profile."""

    allowed: bool
    profile: str
    amount_usd: str
    equity_usd: str | None
    limits: PreviewLimitsOut
    percent_cap_usd: str | None
    effective_max_usd: str
    reasons: list[dict[str, Any]]


class TradePolicyPreviewIn(BaseModel):
    """Query inputs for a preview, kept as decimals so precision is never lost."""

    model_config = ConfigDict(extra="forbid")

    profile: str | None = None
    amount_usd: Decimal = Field(gt=0)
    equity_usd: Decimal | None = Field(default=None, ge=0)
