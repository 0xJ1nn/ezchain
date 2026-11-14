"""A per-agent spending cap, measured on the ledger.

An agent bucket is a *cap on net cash deployed*, not a second account and not a balance
field. Every journal leg an agent's order writes is attributed to that agent's bucket, and
the bucket's deployment is the net cash those legs moved: money spent from the account is
positive, money returned by an exit is negative. Because the figure is replayed from
``ledger_entries`` (CONTRACT I1), it survives a restart, cannot drift from the journal, and
no mutable scalar can be edited to grant more room.

The invariant the gate enforces is one line: **net cash deployed never exceeds the cap**.
Spending up to the cap is allowed, spending past it is refused ``AGENT_BUCKET_EXCEEDED``
before a quote, and an exit is never refused — a cap may not trap a position (the
reduce-only rule). Lowering a cap below what is already deployed therefore refuses new
entries while every exit still settles, and the deployment falls as the agent returns cash.

This module is arithmetic only. The caller reads the journal and the stored cap; it is
never asked to trust a cached number.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .reason_codes import (
    AGENT_BUCKET_CAP_INVALID,
    AGENT_BUCKET_EXCEEDED,
    AGENT_BUCKET_REQUIRES_PERSON,
    AGENT_BUCKET_UNKNOWN,
    AGENT_BUCKET_UNREADABLE,
)
from .types import RiskReason


@dataclass(frozen=True)
class BucketState:
    """One agent's cap and what the ledger says it has deployed.

    ``cap_usd`` is null when the owner has set no cap. That is the unchanged default: the
    agent's orders are still governed by cash, risk, the kill switch and the approval
    tier, but no bucket limit applies. A cap is never invented for an agent — an owner
    sets one, and until then the honest answer is "no cap".

    ``deployed_usd`` is ``-(net cash attributed to the bucket)``. It is negative when the
    agent has returned more cash than it deployed (an exit at a gain), which is how a
    profitable exit funds the next entry without the owner raising the cap.
    """

    agent_id: str
    cap_usd: Decimal | None
    deployed_usd: Decimal

    @property
    def capped(self) -> bool:
        return self.cap_usd is not None

    @property
    def available_usd(self) -> Decimal | None:
        """What may still be deployed. Null when no cap is set."""
        if self.cap_usd is None:
            return None
        return self.cap_usd - self.deployed_usd

    def to_dict(self) -> dict[str, Any]:
        available = self.available_usd
        return {
            "agent_id": self.agent_id,
            "cap_usd": None if self.cap_usd is None else str(self.cap_usd),
            "deployed_usd": str(self.deployed_usd),
            "available_usd": None if available is None else str(available),
            "capped": self.capped,
            "mode": "paper",
        }


def bucket_state(
    agent_id: str, *, cap_usd: Decimal | None, bucket_cash_usd: Decimal
) -> BucketState:
    """Build the state from the stored cap and the replayed bucket cash.

    The sign flip is the only arithmetic here: a bucket's cash is what the account lost
    to the agent's orders, so its negation is what those orders put at risk.
    """
    return BucketState(agent_id=agent_id, cap_usd=cap_usd, deployed_usd=-bucket_cash_usd)


def check_bucket(
    state: BucketState, requested_usd: Decimal, *, fee_usd: Decimal = Decimal(0)
) -> RiskReason | None:
    """The refusal for one entry, or ``None`` when it fits inside the cap.

    ``fee_usd`` is the fill fee when the caller knows it (inside the fill window) and zero
    at the pre-quote gate, where the venue cost is not yet known. The pre-quote check is
    therefore the notional against the cap and the fill-window check is the worst case:
    notional plus fee. Both compare against the same replayed deployment, so neither can
    be widened by a stale read.
    """
    available = state.available_usd
    if available is None:
        return None
    worst_case = requested_usd + fee_usd
    if worst_case <= available:
        return None
    return RiskReason(
        AGENT_BUCKET_EXCEEDED,
        detail={
            "agent_id": state.agent_id,
            "cap_usd": str(state.cap_usd),
            "deployed_usd": str(state.deployed_usd),
            "available_usd": str(available),
            "requested_usd": str(requested_usd),
            "fee_usd": str(fee_usd),
            # The worst case in dollars: what this entry would cost the bucket at most.
            "worst_case_usd": str(worst_case),
        },
    )


__all__ = [
    "AGENT_BUCKET_CAP_INVALID",
    "AGENT_BUCKET_EXCEEDED",
    "AGENT_BUCKET_REQUIRES_PERSON",
    "AGENT_BUCKET_UNKNOWN",
    "AGENT_BUCKET_UNREADABLE",
    "BucketState",
    "bucket_state",
    "check_bucket",
]
