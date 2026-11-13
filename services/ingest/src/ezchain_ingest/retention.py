"""How long market evidence is kept, in one place, and the pruning that enforces it.

The policy is data, not a SQL literal: the raw observation window and the 1-minute candle
window are bounded numbers with one home (CONTRACT I7), read from the environment and
validated once. Pruning is an explicit operator action, never a side effect of a write
(CONTRACT I8 — a migration is not a data reset), so :func:`prune` defaults to a dry run and
reports what it *would* delete.

Two rules the pruning keeps:

* An event is aged by ``source_observed_at`` — when the source says it happened — never by
  the ingest clock. A late report cannot keep an old observation alive.
* An unpublished outbox row is never deleted. Dropping it would lose a message for a row
  that committed; only published rows age out with the raw window.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ezchain_api.models import MarketCandleRow, MarketEventRow, MarketOutboxRow

#: The window the issue fixes for raw observations: a week minimum, a month maximum.
MIN_RAW_DAYS = 7
MAX_RAW_DAYS = 30
DEFAULT_RAW_DAYS = 14

#: 1-minute bars are the finest resolution worth keeping a year of; coarser bars last longer.
MIN_CANDLE_1M_DAYS = 30
MAX_CANDLE_1M_DAYS = 3650
DEFAULT_CANDLE_1M_DAYS = 365

#: The only bars this policy truncates. Coarser timeframes carry their own evidence and are
#: left to their owner, so a retention run can never empty a 1d series from a 1m window.
CANDLE_TIMEFRAME = "1m"

ENV_RAW_DAYS = "EZCHAIN_RETENTION_RAW_DAYS"
ENV_CANDLE_1M_DAYS = "EZCHAIN_RETENTION_CANDLE_1M_DAYS"


class RetentionPolicyError(ValueError):
    """A retention window is outside the range the platform accepts."""


class RetentionError(RuntimeError):
    """Retention could not be planned or applied."""


@dataclass(frozen=True)
class RetentionPolicy:
    """The platform's retention windows, validated at construction.

    ``raw_days`` covers normalised ``market_events`` and delivered outbox rows;
    ``candle_1m_days`` covers closed 1-minute bars in ``market_candles``.
    """

    raw_days: int = DEFAULT_RAW_DAYS
    candle_1m_days: int = DEFAULT_CANDLE_1M_DAYS

    def __post_init__(self) -> None:
        if isinstance(self.raw_days, bool) or not MIN_RAW_DAYS <= self.raw_days <= MAX_RAW_DAYS:
            raise RetentionPolicyError(
                f"raw retention must be between {MIN_RAW_DAYS} and {MAX_RAW_DAYS} days, "
                f"got {self.raw_days!r}"
            )
        if (
            isinstance(self.candle_1m_days, bool)
            or not MIN_CANDLE_1M_DAYS <= self.candle_1m_days <= MAX_CANDLE_1M_DAYS
        ):
            raise RetentionPolicyError(
                f"1m candle retention must be between {MIN_CANDLE_1M_DAYS} and "
                f"{MAX_CANDLE_1M_DAYS} days, got {self.candle_1m_days!r}"
            )

    def to_dict(self) -> dict[str, int]:
        return {"raw_days": self.raw_days, "candle_1m_days": self.candle_1m_days}

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> RetentionPolicy:
        """Read the policy from the environment, refusing an out-of-range window."""
        source = os.environ if environ is None else environ

        def read(name: str, default: int) -> int:
            raw = source.get(name)
            if raw is None or not raw.strip():
                return default
            try:
                return int(raw)
            except ValueError as exc:
                raise RetentionPolicyError(f"{name} must be an integer, got {raw!r}") from exc

        return cls(
            raw_days=read(ENV_RAW_DAYS, DEFAULT_RAW_DAYS),
            candle_1m_days=read(ENV_CANDLE_1M_DAYS, DEFAULT_CANDLE_1M_DAYS),
        )


@dataclass(frozen=True)
class RetentionResult:
    """What a prune would delete (``applied=False``) or did delete.

    ``candle_table_present`` is false on a database that never ran the candle revision;
    pruning reports that instead of failing, because absence is a fact about the schema, not
    an error in the data.
    """

    applied: bool
    raw_cutoff: datetime
    candle_cutoff: datetime
    raw_events: int
    published_outbox: int
    candles_1m: int
    candle_table_present: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "raw_cutoff": self.raw_cutoff.isoformat(),
            "candle_cutoff": self.candle_cutoff.isoformat(),
            "raw_events": self.raw_events,
            "published_outbox": self.published_outbox,
            "candles_1m": self.candles_1m,
            "candle_table_present": self.candle_table_present,
        }


def _count(connection: Connection | Session, predicate: Any, entity: Any) -> int:
    statement = sa.select(sa.func.count()).select_from(entity).where(predicate)
    return int(connection.execute(statement).scalar_one())


def _has_candle_table(connection: Connection | Session) -> bool:
    """Whether the candle revision has run. ``inspect`` needs the engine behind a session."""
    bind = connection.get_bind() if isinstance(connection, Session) else connection
    return sa.inspect(bind).has_table(MarketCandleRow.__tablename__)


def prune(
    connection: Connection | Session,
    policy: RetentionPolicy | None = None,
    *,
    now: datetime | None = None,
    apply: bool = False,
) -> RetentionResult:
    """Count — and with ``apply``, delete — rows older than the policy allows.

    The caller owns the transaction, so it decides whether a delete commits. A dry run
    issues only ``SELECT`` statements and is safe to run against production to plan an
    operator window; ``apply=True`` deletes in the same transaction.
    """
    active = RetentionPolicy.from_env() if policy is None else policy
    clock = now or datetime.now(UTC)
    raw_cutoff = clock - timedelta(days=active.raw_days)
    candle_cutoff = clock - timedelta(days=active.candle_1m_days)

    raw_predicate = MarketEventRow.source_observed_at < raw_cutoff
    outbox_predicate = sa.and_(
        MarketOutboxRow.published_at.is_not(None),
        MarketOutboxRow.created_at < raw_cutoff,
    )
    candle_predicate = sa.and_(
        MarketCandleRow.timeframe == CANDLE_TIMEFRAME,
        MarketCandleRow.open_time < candle_cutoff,
    )
    try:
        raw_count = _count(connection, raw_predicate, MarketEventRow)
        outbox_count = _count(connection, outbox_predicate, MarketOutboxRow)
        present = _has_candle_table(connection)
        candle_count = _count(connection, candle_predicate, MarketCandleRow) if present else 0

        if apply:
            if raw_count:
                connection.execute(sa.delete(MarketEventRow).where(raw_predicate))
            if outbox_count:
                connection.execute(sa.delete(MarketOutboxRow).where(outbox_predicate))
            if candle_count:
                connection.execute(sa.delete(MarketCandleRow).where(candle_predicate))
    except SQLAlchemyError as exc:
        raise RetentionError(f"could not prune market data: {exc}") from exc

    return RetentionResult(
        applied=apply,
        raw_cutoff=raw_cutoff,
        candle_cutoff=candle_cutoff,
        raw_events=raw_count,
        published_outbox=outbox_count,
        candles_1m=candle_count,
        candle_table_present=present,
    )


__all__ = [
    "CANDLE_TIMEFRAME",
    "DEFAULT_CANDLE_1M_DAYS",
    "DEFAULT_RAW_DAYS",
    "ENV_CANDLE_1M_DAYS",
    "ENV_RAW_DAYS",
    "MAX_CANDLE_1M_DAYS",
    "MAX_RAW_DAYS",
    "MIN_CANDLE_1M_DAYS",
    "MIN_RAW_DAYS",
    "RetentionError",
    "RetentionPolicy",
    "RetentionPolicyError",
    "RetentionResult",
    "prune",
]
