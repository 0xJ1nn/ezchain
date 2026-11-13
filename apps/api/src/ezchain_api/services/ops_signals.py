"""Sample the operational state the alert rules read.

The four families in ``infra/prometheus/alerts.yml`` must come from state the process can
prove, never from a constant. Each one is read here from the system that already owns it:

* source freshness — the newest observation per persisted source, classified by the same
  :class:`FreshnessPolicy` the order gate uses, so the alert and the refusal agree;
* breaker state — the live :class:`CircuitBreaker` of every provider and the market
  monitor, so a tripped circuit is visible instead of only logged;
* queue depth — the bounded provider-budget queues and the persisted market outbox, the
  two queues that exist in this deployment;
* ledger drift — the failing checks of the reconciliation report, the drift detector that
  already gates paper accounting.

A scrape is read-only. Anything the process cannot establish is omitted (or ``NaN`` when
it is an age), so a broken read is absence, never a confident zero.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ezchain_core import FreshnessPolicy, SourceRef, utcnow
from ezchain_core.freshness import DEFAULT_MAX_AGE_BY_CLASS

from ..models import MarketCandleRow, MarketOutboxRow, MarketSnapshotRow
from ..observability import OpsSnapshot, SourceFreshnessSample
from . import reconciliation

# Source classes whose newest persisted observation this process can read. ``quote`` and
# ``feed`` have budgets but no persisted observation table, so they are absent rather than
# reported at a made-up age.
PERSISTED_SOURCE_CLASSES = ("mark", "candle")

MARK_SOURCE_CLASS = "mark"
CANDLE_SOURCE_CLASS = "candle"
PROVIDER_QUEUE_PREFIX = "provider"
MARKET_OUTBOX_QUEUE = "market_outbox"


def _aware(value: datetime) -> datetime:
    """Persisted timestamps are UTC; SQLite hands them back naive."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _newest_per_source(
    session: Session, now: datetime, policy: FreshnessPolicy
) -> list[SourceFreshnessSample]:
    """One sample per persisted source name, from the newest row it wrote.

    ``market_snapshots`` records the source's own observation time, so the mark age is
    derived rather than stored (CONTRACT I9). A candle row's ``fetched_at`` is this
    process's last successful fetch from that source, which is the candle class's
    freshness signal. A source that writes both classes reports each separately: the
    budgets differ.
    """
    samples: list[SourceFreshnessSample] = []
    mark_rows = session.execute(
        select(MarketSnapshotRow.source, func.max(MarketSnapshotRow.observed_at)).group_by(
            MarketSnapshotRow.source
        )
    ).all()
    for source, newest in mark_rows:
        if newest is None or not source:
            continue
        samples.append(_sample(policy, str(source), MARK_SOURCE_CLASS, newest, now))

    candle_rows = session.execute(
        select(MarketCandleRow.source, func.max(MarketCandleRow.fetched_at)).group_by(
            MarketCandleRow.source
        )
    ).all()
    for source, newest in candle_rows:
        if newest is None or not source:
            continue
        samples.append(_sample(policy, str(source), CANDLE_SOURCE_CLASS, newest, now))
    return samples


def _sample(
    policy: FreshnessPolicy,
    source: str,
    source_class: str,
    observed: datetime,
    now: datetime,
) -> SourceFreshnessSample:
    """Classify one source's newest observation exactly as the read gate would."""
    reference = SourceRef(name=source, url=None, observed_at=_aware(observed))
    verdict = policy.classify(reference, source_class=source_class, now=now)
    # A future-dated observation is a clock fault: its arithmetic age would read as
    # fresh, so the age is unknown instead of a confident zero.
    age = None if verdict.status in ("future", "unknown") else verdict.age_seconds
    return SourceFreshnessSample(
        source=source,
        source_class=source_class,
        age_seconds=age,
    )


def _budgets(policy: FreshnessPolicy) -> dict[str, float]:
    configured = {name: float(budget) for name, budget in policy.max_age_by_class.items()}
    # Keep every documented class, so the alert's join has a budget for each.
    for name, budget in DEFAULT_MAX_AGE_BY_CLASS.items():
        configured.setdefault(name, float(budget))
    return configured


def _breaker_states(quote_router: Any | None, market_monitor: Any | None) -> dict[str, str]:
    """Live breaker state per provider. Absent sources are absent, never ``closed``."""
    states: dict[str, str] = {}
    if quote_router is not None:
        states.update(quote_router.breakers())
    if market_monitor is not None:
        provider = getattr(market_monitor, "provider_name", None)
        breaker = getattr(market_monitor, "breaker", None)
        if isinstance(provider, str) and breaker is not None:
            states[provider] = breaker.state
    return states


def _queue_depths(session: Session, provider_budget: Any | None) -> dict[str, int]:
    """Queues that exist: bounded provider budgets and the persisted market outbox."""
    depths: dict[str, int] = {}
    if provider_budget is not None:
        for provider, waiting in provider_budget.depths().items():
            depths[f"{PROVIDER_QUEUE_PREFIX}:{provider}"] = int(waiting)
    pending = session.scalar(
        select(func.count())
        .select_from(MarketOutboxRow)
        .where(MarketOutboxRow.published_at.is_(None))
    )
    depths[MARKET_OUTBOX_QUEUE] = int(pending or 0)
    return depths


def _ledger_drift(session: Session) -> int:
    """How many reconciliation checks fail right now."""
    report = reconciliation.reconciliation(session)
    checks = report.get("checks") or ()
    return sum(1 for check in checks if not check.get("ok"))


def build_ops_snapshot(
    session: Session,
    *,
    quote_router: Any | None = None,
    market_monitor: Any | None = None,
    provider_budget: Any | None = None,
    policy: FreshnessPolicy | None = None,
    now: Callable[[], datetime] = utcnow,
) -> OpsSnapshot | None:
    """Sample all four families from live state, or ``None`` when the read fails.

    ``policy`` is the instance the order gate classifies with, so the emitted budget is
    the one a refused read was measured against. ``None`` means the caller renders no ops
    family at all: an unreachable database must not be reported as zero drift and an
    empty queue.
    """
    policy = policy or FreshnessPolicy()
    current = now()
    try:
        samples = _newest_per_source(session, current, policy)
        queues = _queue_depths(session, provider_budget)
        drift = _ledger_drift(session)
    except Exception:
        session.rollback()
        return None
    return OpsSnapshot(
        source_freshness=tuple(samples),
        freshness_budgets=_budgets(policy),
        breakers=_breaker_states(quote_router, market_monitor),
        queues=queues,
        ledger_drift_entries=drift,
    )


__all__ = [
    "MARKET_OUTBOX_QUEUE",
    "PERSISTED_SOURCE_CLASSES",
    "build_ops_snapshot",
]
