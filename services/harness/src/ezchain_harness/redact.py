"""Redaction for everything this service writes down.

A record line, a log line and a rendered page all pass through :func:`redact` before they
hold a value from a request. The client registers the live credential's value with the
redactor as well, so an exact-match scrub runs before the shape patterns: a key the provider
formats unusually is still caught, because the thing being replaced is the string itself and
not an imitation of its shape.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

REDACTED = "[redacted]"

_SECRET_SHAPES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bezk_[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\b(?:bearer|authorization: bearer)\s+[A-Za-z0-9._\-]{8,}"),
)


def redact(text: str, *, secrets: Sequence[str] = ()) -> str:
    out = text
    for secret in secrets:
        if secret:
            out = out.replace(secret, REDACTED)
    for shape in _SECRET_SHAPES:
        out = shape.sub(REDACTED, out)
    return out


__all__ = ["REDACTED", "redact"]
