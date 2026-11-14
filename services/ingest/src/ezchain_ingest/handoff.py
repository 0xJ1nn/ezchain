"""The buffered handoff between a socket reader and an entry read.

An observation leaves the wire at one instant and may be served at another. The
rule this module states once is that an observation is only servable while it is
at or under the staleness budget *on its own* handoff clock. A feed that has just
recovered says nothing about the items that piled up while it was down: each of
them is refused and counted on its own age, never served on the newest item's
liveness.

The module is deliberately free of the optional ``pumpfire`` package, so the entry
rule can be exercised on a machine that never installed it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ezchain_adapters.firehose import FirehoseEvent


@dataclass(frozen=True)
class PendingObservation:
    """One buffered observation plus the handoff clock instant it was proved live.

    ``clock`` is the reader's monotonic clock — the same timebase the staleness
    gate measures age on — not the chain timestamp. That is what makes an item's
    age comparable with a gate decision at read time.
    """

    event: FirehoseEvent
    clock: float

    def age_seconds(self, now: float) -> float:
        return max(0.0, now - self.clock)

    def is_stale(self, stale_after_s: float, now: float) -> bool:
        return self.age_seconds(now) > stale_after_s


def split_stale(
    pending: Iterable[PendingObservation],
    *,
    now: float,
    stale_after_s: float,
) -> tuple[list[FirehoseEvent], int]:
    """Split drained observations into servable entries and a refusal count.

    Order is preserved and every observation older than the budget is counted, so
    a recovered feed serves only what it can still defend and the operator can see
    how much of the backlog was dropped for being old.
    """
    fresh: list[FirehoseEvent] = []
    refused = 0
    for item in pending:
        if item.is_stale(stale_after_s, now):
            refused += 1
            continue
        fresh.append(item.event)
    return fresh, refused


__all__ = ["PendingObservation", "split_stale"]
