"""Journal sinks: the only way a fill reaches the ledger.

The pipeline never writes ``ledger_entries`` itself. It builds a :class:`Journal` from
the venue receipt and hands it to a sink, which appends. There is no update or delete,
and no sink exposes a mutable balance: a balance is always a replay of the appended
entries.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ezchain_core import LedgerEntry, assert_balanced
from ezchain_core.execution import Journal


@runtime_checkable
class JournalSink(Protocol):
    """Append one journal. A failure must surface, never be swallowed."""

    def append(self, journal: Journal) -> None: ...


class MemoryJournal:
    """An append-only in-memory journal, for the fake venue and for tests."""

    def __init__(self) -> None:
        self._groups: list[tuple[str, tuple[LedgerEntry, ...]]] = []

    def append(self, journal: Journal) -> None:
        assert_balanced(journal.entries)
        self._groups.append((journal.group_id, tuple(journal.entries)))

    @property
    def groups(self) -> tuple[str, ...]:
        return tuple(group_id for group_id, _ in self._groups)

    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        return tuple(entry for _, entries in self._groups for entry in entries)

    def entries_for(self, group_id: str) -> tuple[LedgerEntry, ...]:
        for existing, entries in self._groups:
            if existing == group_id:
                return entries
        raise KeyError(group_id)

    def __len__(self) -> int:
        return len(self._groups)


__all__ = ["JournalSink", "MemoryJournal"]
