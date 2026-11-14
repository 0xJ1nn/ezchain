"""The known credential shapes, and the one function that removes them.

The trace is written by a loop whose input is attacker-influenced by construction: a tool
message, a tool name or an exception string can carry a credential. This module is the list of
shapes recognised on write and replaced with ``[redacted]``.

It is a known-shape filter, not a secret detector. A credential that matches no shape here is
stored as it arrived, which is why the trace digests tool arguments and the prompt instead of
storing their text, and why ADR-0042's rule is that the agent key and the model-provider
credential never enter a prompt or a trace in the first place.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .codec import TraceEncodingError

REDACTED = "[redacted]"

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # An ``ezk_`` key pasted into an identifier (``mcp_ezk_…``) is still a key, so the prefix
    # patterns carry no leading word boundary: ``_`` is a word character and would hide it.
    re.compile(r"ezk_[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"gho_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"glpat-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"npm_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)\bAWS_SECRET_ACCESS_KEY\s*[=:]\s*[A-Za-z0-9/+=]{8,}"),
    re.compile(r"(?i)\bx-api-key\s*[:=]\s*[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)[?&](?:access_)?token=[^&\s\"']{8,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
)


def _redact_text(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def redact(value: Any) -> Any:
    """Replace known credential shapes, recursively, in values and in mapping keys.

    Never mutates the caller's data. A tuple is refused rather than turned into a list: the
    canonical encoding distinguishes one from a list, and quietly normalising it here would let
    two different field values share a digest. Two distinct keys that collapse to the same
    redacted name would make the record ambiguous — one field silently overwriting the other —
    so that is refused too.
    """
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, Mapping):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            name = _redact_text(key) if isinstance(key, str) else key
            if name in out:
                raise TraceEncodingError(
                    f"two field names collapse to {name!r} once redacted, so one hides the other"
                )
            out[name] = redact(item)
        return out
    if isinstance(value, tuple):
        raise TraceEncodingError(
            "a tuple has no canonical form; use a list so [] means exactly one thing"
        )
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [redact(item) for item in value]
    return value


__all__ = ["REDACTED", "redact"]
