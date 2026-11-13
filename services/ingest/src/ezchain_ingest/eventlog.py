"""Recorded market observations: one ``MarketEvent`` per JSONL line.

A recording is the input a replay is judged against, so it is written through the
contract's own serialiser and never stores a freshness verdict. The writer is atomic
(a scribbled recording cannot be mistaken for a complete one) and the reader is strict:
a corrupt, wrongly-versioned or unit-less line fails the whole replay instead of
quietly shrinking the sample.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from ezchain_core import MarketEvent, MarketEventError


class EventLogError(ValueError):
    """A recorded market-event log cannot be read or replayed."""


def write_events(path: str | Path, events: Iterable[MarketEvent]) -> int:
    """Write normalised events to ``path`` and return the line count.

    ``to_dict()`` without a clock is the recorded form, so no age or staleness is
    committed; both are derived when the file is read.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":")) for event in events
    ]
    payload = "\n".join(lines)
    if payload:
        payload += "\n"
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(target)
    return len(lines)


def read_events(path: str | Path) -> tuple[MarketEvent, ...]:
    """Read a recording into contract events, or raise :class:`EventLogError`."""
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise EventLogError(f"recording is missing: {source}") from exc
    events: list[MarketEvent] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EventLogError(f"line {number}: not valid JSON") from exc
        try:
            events.append(MarketEvent.from_dict(raw))
        except (MarketEventError, TypeError, ValueError) as exc:
            raise EventLogError(f"line {number}: {exc}") from exc
    return tuple(events)


__all__ = ["EventLogError", "read_events", "write_events"]
