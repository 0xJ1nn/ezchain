"""The session trace: append-only, hash-chained and replayable.

ADR-0042 names the runtime's audit trail: one append-only trace per session, in the runtime's
own store, never in the platform database, redacted on write, replayable against the recorded
results. This package is that store.

**Append only.** :class:`TraceSink` has one operation, ``append``, beside the sealing ``key`` it
writes with. The stores add ``read`` and ``sessions`` and nothing else: there is no update, no
delete and no rewrite path anywhere in the API.

**Tamper evidence, and exactly what it proves.** Every event carries the digest of the event
before it, and an event's digest is taken over its canonical encoding (:mod:`.codec`) with the
store's key when it has one (:mod:`.key`).

* **Keyed** — ``EZCHAIN_TRACE_KEY``, ``FileTraceLog(root, key_path=...)`` or a key handed in
  directly, from outside the trace directory. The digest is ``hmac-sha256:<hex>``, so anyone
  holding the files but not the key cannot alter, reorder, drop or insert an event, cannot cut
  the tail and forge a terminal outcome, and cannot rewrite the chain from GENESIS: none of
  those digests can be recomputed. :func:`verify_events` refuses a keyed trace when no key is
  supplied and an unkeyed trace when one is, so a rewritten chain cannot shed its prefix.
* **Unkeyed** — no key configured, which is the default. The digest is a bare SHA-256: a
  checksum chain. It detects a corrupted or carelessly edited file and proves nothing about
  authorship; anyone who can write the file can rewrite all of it, or cut the tail and append a
  forged ``outcome``, and verification passes. Such a trace is chain-only, not tamper-evident.
* **Truncation** is not a forgery, so a key alone does not catch it. Persist
  :func:`anchor_of` where the trace's writer cannot rewrite it and pass it to
  :func:`verify_events`; a file shorter, longer or differently headed than its anchor is
  refused.

Neither mode notarises time: a keyed trace proves the events were sealed by a holder of the
key, not when.

**Integrity is not completeness.** :func:`verify_events` checks the events it is given, so an
empty sequence and any prefix of a real trace verify clean; without an anchor it cannot know how
long the trace was meant to be. Deciding a trace is a *finished* session is :func:`replay`'s
job, and it raises :class:`TraceIncomplete` when the prefix dangles.
"""

from __future__ import annotations

from .codec import (
    TraceChainBroken,
    TraceEncodingError,
    TraceError,
    TraceIncomplete,
    TraceStoreError,
    TraceWriteFailed,
    canonical,
    digest_json,
    digest_text,
    money,
)
from .events import (
    GENESIS,
    TRACE_SCHEMA,
    TraceAnchor,
    TraceEvent,
    TraceEventKind,
    TraceSink,
    anchor_of,
    verify_events,
)
from .key import MIN_KEY_BYTES, TRACE_KEY_ENV, is_keyed, resolve_key, seal
from .recorder import TraceRecorder
from .redaction import REDACTED, redact
from .replay import SessionSummary, replay, summarize
from .store import FileTraceLog, MemoryTraceLog

__all__ = [
    "GENESIS",
    "MIN_KEY_BYTES",
    "REDACTED",
    "TRACE_KEY_ENV",
    "TRACE_SCHEMA",
    "FileTraceLog",
    "MemoryTraceLog",
    "SessionSummary",
    "TraceAnchor",
    "TraceChainBroken",
    "TraceEncodingError",
    "TraceError",
    "TraceEvent",
    "TraceEventKind",
    "TraceIncomplete",
    "TraceRecorder",
    "TraceSink",
    "TraceStoreError",
    "TraceWriteFailed",
    "anchor_of",
    "canonical",
    "digest_json",
    "digest_text",
    "is_keyed",
    "money",
    "redact",
    "replay",
    "resolve_key",
    "seal",
    "summarize",
    "verify_events",
]
