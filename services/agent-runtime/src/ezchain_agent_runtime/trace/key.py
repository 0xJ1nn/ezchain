"""Where a trace's sealing key comes from, and how a digest is sealed with it.

A hash chain with no key is a checksum: whoever can write the file can also recompute it, so a
rewritten chain still verifies. The key is what turns the chain into evidence, and it must live
outside the trace directory — a key stored beside the trace protects nothing.

Three sources, in order: a key handed to the store, a key file the caller names, and
``EZCHAIN_TRACE_KEY``. No key at all is allowed and is the default; the digest is then a bare
SHA-256 and the trace is explicitly chain-only, which the store reports through ``key`` and the
docs state plainly.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Mapping
from pathlib import Path

TRACE_KEY_ENV = "EZCHAIN_TRACE_KEY"
MIN_KEY_BYTES = 32
KEYED_PREFIX = "hmac-sha256:"


def checked_key(key: bytes, source: str) -> bytes:
    """A key that is long enough to be one, or ``ValueError`` naming where it came from."""
    if len(key) < MIN_KEY_BYTES:
        raise ValueError(
            f"{source} holds {len(key)} bytes; a trace key must be at least "
            f"{MIN_KEY_BYTES} (openssl rand -hex 32)"
        )
    return key


def key_from_environment(environ: Mapping[str, str] | None = None) -> bytes | None:
    """The key in ``EZCHAIN_TRACE_KEY``, or ``None`` when the variable is absent.

    A variable that is set but empty is a mistake, not a request for an unkeyed trace, so it is
    refused instead of silently downgrading the chain.
    """
    source = os.environ if environ is None else environ
    raw = source.get(TRACE_KEY_ENV)
    if raw is None:
        return None
    return checked_key(raw.strip().encode("utf-8"), TRACE_KEY_ENV)


def key_from_file(path: str | Path, *, root: str | Path) -> bytes:
    """The key stored at ``path``, refusing a key that sits inside the trace directory."""
    key_path = Path(path).expanduser()
    trace_root = Path(root).expanduser()
    if key_path.resolve().is_relative_to(trace_root.resolve()):
        raise ValueError(
            f"trace key {key_path} is inside the trace directory {trace_root}; "
            "a key stored beside the trace protects nothing"
        )
    try:
        key = key_path.read_bytes().strip()
    except OSError as exc:
        raise ValueError(f"trace key {key_path} cannot be read: {exc}") from exc
    return checked_key(key, str(key_path))


def resolve_key(
    *,
    root: str | Path,
    key: bytes | None = None,
    key_path: str | Path | None = None,
) -> bytes | None:
    """The sealing key for a store rooted at ``root``: explicit, file, environment, or none."""
    if key is not None and key_path is not None:
        raise ValueError("pass a trace key or a key path, not both")
    if key is not None:
        return checked_key(key, "the supplied trace key")
    if key_path is not None:
        return key_from_file(key_path, root=root)
    return key_from_environment()


def seal(text: str, key: bytes | None) -> str:
    """The digest of one canonical record: keyed when there is a key, bare SHA-256 when not."""
    payload = text.encode("utf-8")
    if key is None:
        return hashlib.sha256(payload).hexdigest()
    return KEYED_PREFIX + hmac.new(key, payload, hashlib.sha256).hexdigest()


def is_keyed(digest: str) -> bool:
    return digest.startswith(KEYED_PREFIX)


__all__ = [
    "KEYED_PREFIX",
    "MIN_KEY_BYTES",
    "TRACE_KEY_ENV",
    "checked_key",
    "is_keyed",
    "key_from_environment",
    "key_from_file",
    "resolve_key",
    "seal",
]
