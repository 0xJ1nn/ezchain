"""The broadcast outbox and the pass that reconciles it with the venue.

A fill can move money at the venue and leave no ledger entry if the process dies between
``submit`` and ``sink.append``. The ledger's one-write-path rule says nothing may write
``ledger_entries`` outside the sink, so the claim lives in a separate append-only outbox:
a row is written *before* the submit call, and a resolution is appended only after the
outcome is known. Adding a status column to a journal row would give the ledger two
lifecycles and let a reconciliation write accounting directly, which this boundary forbids.

The outbox is a protocol, not a database. :class:`FileAttemptLog` is the durable
implementation: one JSON object per line, flushed and fsynced before ``submit`` returns,
so a crash leaves a trace that a later process can read. :class:`MemoryAttemptLog` is the
in-memory equivalent for tests. Reconciliation reads the claim's persisted inputs, asks
the venue for the transaction's real state where it can answer, and either journals the
missing fill (idempotent on the group id) or leaves the claim pending for another pass.

Three rules keep a reconcile safe while a live pipeline is filling:

* Only claims older than ``min_age_seconds`` are read. A claim for an in-flight submit is
  younger than the pipeline's own deadline, so a live fill is never mistaken for a lost one.
* A journal group is checked before it is appended. A crash after the append but before
  the resolution must not book the same money twice.
* An unavailable or unknown venue answer leaves the claim pending with a typed reason.
  Only a venue that says it never filled resolves a claim as failed; a journal that fails
  to append stays pending too. Nothing invents a zero or a fill.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from ezchain_core import Fill, Journal, exact_product, journal_from_fill, quantize_money, utcnow

from .journal import JournalSink

AttemptState = Literal["broadcast", "journaled", "failed"]
VenueState = Literal["filled", "not_found", "unknown"]
Outcome = Literal["journaled", "failed", "pending", "skipped"]

# Both codes already own a taxonomy row; reconciliation is another emission site for them.
_NOT_FILLED = "VENUE_REJECTED"
_STATUS_UNKNOWN = "RECONCILIATION_FAILED"
_JOURNAL_FAILED = "JOURNAL_FAILED"


@dataclass(frozen=True)
class BroadcastAttempt:
    """The persisted inputs of one order at the moment it was broadcast."""

    id: str
    group_id: str
    chain: str
    token_address: str
    side: Literal["buy", "sell"]
    avg_cost_usd: Decimal
    position_cost_usd: Decimal
    transaction_digest: str | None
    created_at: datetime
    state: AttemptState = "broadcast"
    reason: str | None = None


@dataclass(frozen=True)
class VenueTxStatus:
    """What the venue can prove about one broadcast transaction."""

    state: VenueState
    fill: Fill | None = None

    def __post_init__(self) -> None:
        if self.state == "filled" and self.fill is None:
            raise ValueError("a filled status must carry the venue's receipt")


@dataclass(frozen=True)
class Reconciliation:
    """One claim's disposition. ``pending`` and ``skipped`` are not terminal."""

    attempt_id: str
    outcome: Outcome
    reason: str | None = None
    group_id: str | None = None


@runtime_checkable
class TransactionStatusVenue(Protocol):
    """A venue that can answer what happened to a broadcast transaction."""

    async def transaction_status(
        self, *, order_id: str, transaction_digest: str | None
    ) -> VenueTxStatus: ...


@runtime_checkable
class GroupAwareSink(Protocol):
    """A sink that can say whether a journal group is already booked."""

    def has_group(self, group_id: str) -> bool: ...


@runtime_checkable
class AttemptLog(Protocol):
    """Append a claim, then append its resolution. No row is ever edited or removed."""

    def append(self, attempt: BroadcastAttempt) -> None: ...

    def pending(self, *, before: datetime) -> tuple[BroadcastAttempt, ...]: ...

    def resolve(
        self, attempt_id: str, *, state: Literal["journaled", "failed"], reason: str | None = None
    ) -> None: ...


class MemoryAttemptLog:
    """An append-only attempt log for tests and for in-process deployments."""

    def __init__(self) -> None:
        self._attempts: dict[str, BroadcastAttempt] = {}
        self._resolutions: list[tuple[str, Literal["journaled", "failed"], str | None]] = []
        self._lock = threading.Lock()

    def append(self, attempt: BroadcastAttempt) -> None:
        with self._lock:
            if attempt.id in self._attempts:
                raise ValueError(f"attempt {attempt.id} is already recorded")
            self._attempts[attempt.id] = attempt

    def pending(self, *, before: datetime) -> tuple[BroadcastAttempt, ...]:
        with self._lock:
            resolved = {attempt_id for attempt_id, _, _ in self._resolutions}
            return tuple(
                attempt
                for attempt in self._attempts.values()
                if attempt.id not in resolved and attempt.created_at <= before
            )

    def resolve(
        self, attempt_id: str, *, state: Literal["journaled", "failed"], reason: str | None = None
    ) -> None:
        with self._lock:
            if attempt_id not in self._attempts:
                raise KeyError(attempt_id)
            if any(existing == attempt_id for existing, _, _ in self._resolutions):
                raise ValueError(f"attempt {attempt_id} is already resolved")
            self._resolutions.append((attempt_id, state, reason))


def _encode(attempt: BroadcastAttempt) -> dict[str, Any]:
    row = asdict(attempt)
    row["avg_cost_usd"] = str(attempt.avg_cost_usd)
    row["position_cost_usd"] = str(attempt.position_cost_usd)
    row["created_at"] = attempt.created_at.isoformat()
    return row


def _decode(row: dict[str, Any]) -> BroadcastAttempt:
    return BroadcastAttempt(
        id=row["id"],
        group_id=row["group_id"],
        chain=row["chain"],
        token_address=row["token_address"],
        side=row["side"],
        avg_cost_usd=Decimal(row["avg_cost_usd"]),
        position_cost_usd=Decimal(row["position_cost_usd"]),
        transaction_digest=row["transaction_digest"],
        created_at=datetime.fromisoformat(row["created_at"]),
        state=row.get("state", "broadcast"),
        reason=row.get("reason"),
    )


class FileAttemptLog:
    """A durable JSONL outbox. Each row is fsynced before the caller broadcasts.

    Resolution rows are appended, never written over an attempt row, so the file is the
    evidence a post-mortem needs: what was sent, when, and what was later established.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _append(self, row: dict[str, Any]) -> None:
        line = json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _rows(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def append(self, attempt: BroadcastAttempt) -> None:
        with self._lock:
            if any(row["type"] == "attempt" and row["id"] == attempt.id for row in self._rows()):
                raise ValueError(f"attempt {attempt.id} is already recorded")
            self._append({"type": "attempt", **_encode(attempt)})

    def pending(self, *, before: datetime) -> tuple[BroadcastAttempt, ...]:
        with self._lock:
            rows = self._rows()
        resolved = {row["id"] for row in rows if row["type"] == "resolution"}
        return tuple(
            _decode(row)
            for row in rows
            if row["type"] == "attempt"
            and row["id"] not in resolved
            and datetime.fromisoformat(row["created_at"]) <= before
        )

    def resolve(
        self, attempt_id: str, *, state: Literal["journaled", "failed"], reason: str | None = None
    ) -> None:
        with self._lock:
            rows = self._rows()
            if not any(row["type"] == "attempt" and row["id"] == attempt_id for row in rows):
                raise KeyError(attempt_id)
            if any(row["type"] == "resolution" and row["id"] == attempt_id for row in rows):
                raise ValueError(f"attempt {attempt_id} is already resolved")
            self._append(
                {
                    "type": "resolution",
                    "id": attempt_id,
                    "state": state,
                    "reason": reason,
                    "resolved_at": utcnow().isoformat(),
                }
            )


@dataclass
class Reconciler:
    """Resolve old claims against the venue, one at a time.

    ``min_age_seconds`` must exceed the pipeline's submit timeout, or a reconcile running
    beside a live fill could ask about a transaction the venue has not settled yet.
    """

    attempts: AttemptLog
    sink: JournalSink
    venue: object
    min_age_seconds: float = 60.0
    clock: Any = None

    async def run(self) -> tuple[Reconciliation, ...]:
        now = (self.clock or utcnow)()
        cutoff = now - timedelta(seconds=self.min_age_seconds)
        returns: list[Reconciliation] = []
        for attempt in self.attempts.pending(before=cutoff):
            returns.append(await self._one(attempt))
        return tuple(returns)

    async def _one(self, attempt: BroadcastAttempt) -> Reconciliation:
        if not isinstance(self.venue, TransactionStatusVenue):
            return Reconciliation(attempt.id, "pending", _STATUS_UNKNOWN)
        try:
            status = await self.venue.transaction_status(
                order_id=attempt.id, transaction_digest=attempt.transaction_digest
            )
        except Exception:  # noqa: BLE001 - an unreadable venue is not a 'not filled'
            return Reconciliation(attempt.id, "pending", _STATUS_UNKNOWN)
        if status.state == "unknown":
            return Reconciliation(attempt.id, "pending", _STATUS_UNKNOWN)
        if status.state == "not_found":
            self._resolve(attempt.id, "failed", _NOT_FILLED)
            return Reconciliation(attempt.id, "failed", _NOT_FILLED)

        fill = status.fill
        if fill is None or not fill.bookable:
            return Reconciliation(attempt.id, "pending", "UNBOOKABLE_RECEIPT")
        if fill.order_id != attempt.id or f"fill:{fill.order_id}" != attempt.group_id:
            return Reconciliation(attempt.id, "pending", _STATUS_UNKNOWN)
        journal = _journal_from_attempt(attempt, fill)
        if isinstance(self.sink, GroupAwareSink) and self.sink.has_group(attempt.group_id):
            self._resolve(attempt.id, "journaled")
            return Reconciliation(attempt.id, "journaled", group_id=attempt.group_id)
        try:
            self.sink.append(journal)
        except Exception:  # noqa: BLE001 - the claim stays pending; a later pass retries
            return Reconciliation(attempt.id, "pending", _JOURNAL_FAILED)
        self._resolve(attempt.id, "journaled")
        return Reconciliation(attempt.id, "journaled", group_id=attempt.group_id)

    def _resolve(
        self, attempt_id: str, state: Literal["journaled", "failed"], reason: str | None = None
    ) -> None:
        """A resolution that cannot be written is not a reason to lose the outcome.

        The group check makes a second pass idempotent, so a missing resolution only
        costs another reconcile; it must not turn a booked fill into a duplicate one.
        """
        try:
            self.attempts.resolve(attempt_id, state=state, reason=reason)
        except Exception:  # noqa: BLE001 - the ledger, not the outbox, owns the result
            pass


def _journal_from_attempt(attempt: BroadcastAttempt, fill: Fill) -> Journal:
    # The cost basis is derived from the receipt's quantity, not the order's: a partial
    # fill removes only the cost of the tokens that actually moved.
    cost_basis = None
    if fill.side == "sell":
        cost_basis = min(
            attempt.position_cost_usd,
            quantize_money(exact_product(fill.quantity, attempt.avg_cost_usd)),
        )
    return journal_from_fill(
        fill,
        chain=attempt.chain,
        token_address=attempt.token_address,
        avg_cost_usd=attempt.avg_cost_usd,
        cost_basis_usd=cost_basis,
    )


__all__ = [
    "AttemptLog",
    "BroadcastAttempt",
    "FileAttemptLog",
    "GroupAwareSink",
    "MemoryAttemptLog",
    "Reconciler",
    "Reconciliation",
    "TransactionStatusVenue",
    "VenueTxStatus",
]
