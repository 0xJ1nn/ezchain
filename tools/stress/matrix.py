"""Chain matrix helpers: capability discovery, token ranking and outcome classification.

The matrix mode walks *every* chain that advertises ``capabilities.paper_execute`` and
runs one buy/sell round trip per chain. The judgement call — is a rejection a documented
risk decision or an unexplained failure — is pure and lives here, so it is unit-testable
without an API.

A rejection reason is only a skip when *every* comma-separated part is in the documented
risk vocabulary below. An invented reason means the rejection path changed without
updating the contract, which is exactly what a stress harness should notice.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from .checks import _decimal, _mapping, _text

# ``packages/core-py/src/ezchain_core/risk.py`` plus the order service's own pre-quote
# refusals. Keep this list in sync with the code, not with a wiki.
RISK_REJECTION_REASONS: frozenset[str] = frozenset(
    {
        "INVALID_SIDE",
        "INVALID_AMOUNT",
        "ORDER_LIMIT",
        "PAPER_MARK_LIMIT",
        "QUOTE_STALE",
        "PRICE_UNKNOWN",
        "LIQUIDITY_UNKNOWN",
        "LIQUIDITY_TOO_LOW",
        "PAIR_AGE_UNKNOWN",
        "PAIR_TOO_YOUNG",
        "NO_PAIR",
        "NO_SELL_ROUTE",
        "SNAPSHOT_STALE",
        "POSITION_LIMIT",
        "MAX_OPEN_POSITIONS",
        "DAILY_LOSS_LIMIT",
        "INSUFFICIENT_CASH",
        "INSUFFICIENT_POSITION",
        "NO_ROUTE",
        "KILL_SWITCH_ACTIVE",
    }
)

STATUS_OK = "ok"
STATUS_NO_DATA = "no_data"
STATUS_REJECTED = "rejected"
STATUS_FAILED = "failed"


def documented_rejection(reason: object) -> bool:
    """True when a rejection reason is entirely made of documented risk codes."""
    text = _text(reason)
    if text is None:
        return False
    parts = [part.strip() for part in text.split(",") if part.strip()]
    return bool(parts) and all(part in RISK_REJECTION_REASONS for part in parts)


def paper_chain_ids(chains: Sequence[Mapping[str, object]]) -> list[str]:
    """Chain ids that advertise paper execution, in registry order."""
    ids: list[str] = []
    for row in chains:
        chain_id = _text(row.get("id"))
        capabilities = _mapping(row.get("capabilities")) or {}
        if chain_id is not None and capabilities.get("paper_execute") is True:
            ids.append(chain_id)
    return ids


def quote_kind_for(chains: Sequence[Mapping[str, object]], chain: str) -> str:
    """The chain's advertised quote kind, or ``unknown``."""
    for row in chains:
        if row.get("id") != chain:
            continue
        capabilities = _mapping(row.get("capabilities")) or {}
        return _text(capabilities.get("quote_kind")) or "unknown"
    return "unknown"


def _liquidity(row: Mapping[str, object]) -> Decimal | None:
    value = _decimal(row.get("liquidity_usd"))
    if value is None or value < 0:
        return None
    return value


def pick_most_liquid(rows: Sequence[Mapping[str, object]]) -> Mapping[str, object] | None:
    """The most liquid row of a chain's market page.

    Rows with a known liquidity win over rows without one, because an unknown liquidity
    is not a low liquidity and it certainly is not a high one. Ties break on the pair
    address so the choice is reproducible across runs.
    """
    best: Mapping[str, object] | None = None
    best_key: tuple[int, Decimal, str] | None = None
    for row in rows:
        address = _text(row.get("base_token"))
        if address is None:
            continue
        liquidity = _liquidity(row)
        key = (
            0 if liquidity is None else 1,
            liquidity if liquidity is not None else Decimal(0),
            address,
        )
        if best_key is None or key > best_key:
            best, best_key = row, key
    return best


def order_outcome(order: Mapping[str, object] | None) -> tuple[str, str]:
    """``(status, reason)`` of a returned order row, normalised for the matrix table."""
    if order is None:
        return ("missing", "")
    status = _text(order.get("status")) or "unknown"
    reason = _text(order.get("reason")) or ""
    return (status, reason)


@dataclass(frozen=True)
class ChainRow:
    """One row of the per-chain matrix table."""

    chain: str
    status: str
    token: str = ""
    symbol: str = ""
    quote_kind: str = "unknown"
    buy: str = "not_attempted"
    sell: str = "not_attempted"
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "chain": self.chain,
            "status": self.status,
            "token": self.token,
            "symbol": self.symbol,
            "quote_kind": self.quote_kind,
            "buy": self.buy,
            "sell": self.sell,
            "reason": self.reason,
        }


def summarize(rows: Sequence[ChainRow]) -> dict[str, int]:
    """Count rows by status, plus a ``total``."""
    counts: dict[str, int] = {"total": len(rows)}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    return counts


def rejected_skip_status(reason: str) -> str:
    """The table/report label for a documented rejection: ``rejected:<reason>``."""
    compact = ",".join(part.strip() for part in reason.split(",") if part.strip())
    return f"{STATUS_REJECTED}:{compact}" if compact else STATUS_REJECTED


__all__ = [
    "RISK_REJECTION_REASONS",
    "STATUS_FAILED",
    "STATUS_NO_DATA",
    "STATUS_OK",
    "STATUS_REJECTED",
    "ChainRow",
    "documented_rejection",
    "order_outcome",
    "paper_chain_ids",
    "pick_most_liquid",
    "quote_kind_for",
    "rejected_skip_status",
    "summarize",
]
