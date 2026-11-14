"""Where events live: one append-only file per session, or memory behind the same interface.

Both stores share one contract — ``key``, ``append``, ``read``, ``sessions`` — and neither has an
update, delete or rewrite path. The file store additionally names a path and holds the key it
seals with; the memory store exists for a runtime with no filesystem access.
"""

from __future__ import annotations

import copy
import fcntl
import json
import os
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from .codec import TraceStoreError
from .events import (
    GENESIS,
    TRACE_SCHEMA,
    TraceEvent,
    TraceEventKind,
    expect_chain,
    expect_seal,
)
from .key import resolve_key

_RECORD_KEYS = frozenset({"schema", "session_id", "seq", "kind", "at", "fields", "prev", "hash"})
_APPEND_FLAGS = os.O_RDWR | os.O_APPEND | os.O_CREAT
_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class MemoryTraceLog:
    """The same contract in memory, for a runtime with no filesystem access.

    ``read`` hands back a deep copy: a caller can read the log, but it cannot mutate the stored
    events through what it was given and leave the chain describing a session nobody recorded.
    """

    def __init__(self, *, key: bytes | None = None) -> None:
        self._events: dict[str, list[TraceEvent]] = {}
        self._key = key

    @property
    def key(self) -> bytes | None:
        return self._key

    def append(self, event: TraceEvent) -> None:
        expect_seal(self._key, event)
        events = self._events.get(event.session_id, [])
        expect_chain(events[-1] if events else None, event)
        self._events.setdefault(event.session_id, []).append(event)

    def read(self, session_id: str) -> tuple[TraceEvent, ...]:
        try:
            return tuple(copy.deepcopy(self._events[session_id]))
        except KeyError:
            raise TraceStoreError(f"no trace for session {session_id}") from None

    def sessions(self) -> tuple[str, ...]:
        return tuple(sorted(self._events))


class FileTraceLog:
    """One JSON lines file per session, opened append-only or read-only.

    A session id is one file and one chain, and two writers cannot both extend it: the file is
    created ``O_EXCL`` so only one process creates a session, and the read-last-then-append step
    runs inside an exclusive ``flock``, so a writer never appends on top of a tail it read before
    another writer moved it. A second process cannot continue a session it did not record: the
    chain check refuses any event that does not extend the tail found under the lock.

    The sealing key is resolved once, from the constructor, ``key_path`` or
    ``EZCHAIN_TRACE_KEY`` (:mod:`.key`). With no key the file is chain-only and explicitly
    weaker: it detects corruption and proves nothing about who wrote it.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        key: bytes | None = None,
        key_path: str | Path | None = None,
    ) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._key = resolve_key(root=self._root, key=key, key_path=key_path)

    @property
    def key(self) -> bytes | None:
        return self._key

    def path_for(self, session_id: str) -> Path:
        if not _SAFE_SESSION_ID.match(session_id) or ".." in session_id:
            raise TraceStoreError(f"{session_id!r} is not a usable session id")
        return self._root / f"{session_id}.jsonl"

    def append(self, event: TraceEvent) -> None:
        expect_seal(self._key, event)
        path = self.path_for(event.session_id)
        if not path.exists():
            # Checked before the file is created, so a refused first event leaves nothing behind.
            expect_chain(None, event)
        fd = self._open_for_append(path, event.session_id)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            expect_chain(self._last_from(fd, path.stem), event)
            os.write(fd, (event.line() + "\n").encode("utf-8"))
            os.fsync(fd)
        except OSError as exc:
            raise TraceStoreError(f"session {event.session_id}: {exc}") from exc
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _open_for_append(self, path: Path, session_id: str) -> int:
        try:
            return os.open(path, _APPEND_FLAGS | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        except OSError as exc:
            raise TraceStoreError(f"session {session_id}: {exc}") from exc
        try:
            return os.open(path, _APPEND_FLAGS, 0o600)
        except OSError as exc:
            raise TraceStoreError(f"session {session_id}: {exc}") from exc

    def read(self, session_id: str) -> tuple[TraceEvent, ...]:
        path = self.path_for(session_id)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise TraceStoreError(f"no trace for session {session_id}: {exc}") from exc
        return tuple(
            self._parse(session_id, number, line)
            for number, line in enumerate(text.splitlines(), start=1)
            if line.strip()
        )

    def sessions(self) -> tuple[str, ...]:
        return tuple(sorted(path.stem for path in self._root.glob("*.jsonl")))

    def _last_from(self, fd: int, session_id: str) -> TraceEvent | None:
        """The last event on an open, locked file: the read half of read-last-then-append."""
        os.lseek(fd, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while chunk := os.read(fd, 1 << 16):
            chunks.append(chunk)
        return self._last_line(session_id, b"".join(chunks).decode("utf-8"))

    def _last_line(self, session_id: str, text: str) -> TraceEvent | None:
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            return None
        return self._parse(session_id, len(lines), lines[-1])

    def _parse(self, session_id: str, number: int, line: str) -> TraceEvent:
        try:
            stored = json.loads(line)
            if not isinstance(stored, Mapping):
                raise TraceStoreError(f"{session_id}: line {number} is not a trace object")
            if set(stored) != _RECORD_KEYS:
                raise TraceStoreError(
                    f"{session_id}: line {number} carries {sorted(set(stored) - _RECORD_KEYS)} "
                    f"or is missing {sorted(_RECORD_KEYS - set(stored))}"
                )
            if stored["schema"] != TRACE_SCHEMA:
                raise TraceStoreError(f"{session_id}: line {number} has an unknown schema")
            if stored.get("session_id") != session_id:
                raise TraceStoreError(f"{session_id}: line {number} belongs to another session")
            kind = TraceEventKind(stored["kind"])
            at = datetime.fromisoformat(stored["at"])
            previous_hash = stored["prev"]
            digest = stored["hash"]
            fields = stored["fields"]
            sequence = stored["seq"]
            if not isinstance(previous_hash, str) or not isinstance(digest, str):
                raise TraceStoreError(f"{session_id}: line {number} has a non-text digest")
            if isinstance(sequence, bool) or not isinstance(sequence, int):
                raise TraceStoreError(f"{session_id}: line {number} has a non-integer step")
            if not isinstance(fields, Mapping):
                raise TraceStoreError(f"{session_id}: line {number} has no field object")
        except TraceStoreError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise TraceStoreError(
                f"{session_id}: line {number} is not a trace event: {exc}"
            ) from exc
        return TraceEvent(
            session_id=session_id,
            sequence=sequence,
            kind=kind,
            at=at,
            fields=fields,
            previous_hash=previous_hash,
            digest=digest,
        )


__all__ = ["GENESIS", "FileTraceLog", "MemoryTraceLog"]
