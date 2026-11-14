"""ezCHAIN ingest: normalise market data into the one ``MarketEvent`` contract.

The JSONL event log is the replay input; the store is the query path. Both are written from
the same normalised event, so there is one definition of a market number and one
idempotency identity.

The live firehose seam (:class:`FirehoseSource`) is exported lazily: it needs the optional
``pumpfun-firehose`` extra, while retention, replay and the store must import without it.
Install ``ezchain-ingest[firehose]`` to use the live source.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:
    from .source import FirehoseClient as FirehoseClient
    from .source import FirehoseSource as FirehoseSource

__all__ = [
    "EventLogError",
    "FirehoseClient",
    "FirehoseSource",
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

_LAZY = frozenset({"FirehoseClient", "FirehoseSource"})


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        from . import source

        return getattr(source, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
