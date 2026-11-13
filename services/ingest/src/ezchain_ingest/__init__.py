"""ezCHAIN ingest: normalise market data into the one ``MarketEvent`` contract.

The JSONL event log is the replay input; the store is the query path. Both are written from
the same normalised event, so there is one definition of a market number and one
idempotency identity.
"""

from .eventlog import EventLogError, read_events, write_events
from .replay import PairObservation, ReplayHarness, ReplayState, derive_state
from .retention import RetentionPolicy, RetentionPolicyError, RetentionResult, prune
from .store import (
    MARKET_EVENT_TOPIC,
    MarketStoreError,
    latest_events,
    load_events,
    mark_published,
    pending_outbox,
    record_events,
    record_events_transactionally,
)

__all__ = [
    "EventLogError",
    "MARKET_EVENT_TOPIC",
    "MarketStoreError",
    "PairObservation",
    "ReplayHarness",
    "ReplayState",
    "RetentionPolicy",
    "RetentionPolicyError",
    "RetentionResult",
    "derive_state",
    "latest_events",
    "load_events",
    "mark_published",
    "pending_outbox",
    "prune",
    "read_events",
    "record_events",
    "record_events_transactionally",
    "write_events",
]
