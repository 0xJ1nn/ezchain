"""How a provider turn becomes the loop's structured :class:`~ezchain_agent_runtime.Answer`.

The loop accepts a turn that is either tool calls or a final answer, and refuses prose with
neither. A chat provider has no answer type of its own, so the client states the contract in
the outbound system prompt (:data:`ANSWER_CONTRACT`) and parses the final text strictly:
one JSON object, ``summary`` and ``claims``. Text that is not that object yields no answer,
so a model that rambles past its tools fails the session instead of being treated as having
answered.

The parse is a parse, not a repair: fences are stripped because models wrap JSON in them,
and nothing else is guessed. A claim whose value is not a decimal is dropped rather than
coerced, and the loop's own ``resolve_answer`` still checks every claim against a recorded
tool result.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from ezchain_agent_runtime import Answer, NumberClaim

ANSWER_CONTRACT = (
    "When you are done, reply with exactly one JSON object and nothing else: "
    '{"summary": "<one sentence with no digits>", "claims": [{"label": "...", '
    '"value": "<number>", "tool": "<tool name>", "path": "<dotted path>"}]}. '
    "Every number you state belongs in a claim; put no digits in the summary. "
    "Do not wrap the object in prose or a code fence."
)


def _strip_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    body = lines[1:]
    if body and body[-1].strip().startswith("```"):
        body = body[:-1]
    return "\n".join(body).strip()


def _claim(item: Any) -> NumberClaim | None:
    if not isinstance(item, dict):
        return None
    label, tool, path = item.get("label"), item.get("tool"), item.get("path")
    raw = item.get("value")
    if not isinstance(label, str) or not isinstance(tool, str) or not isinstance(path, str):
        return None
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = raw if isinstance(raw, Decimal) else Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite():
        return None
    return NumberClaim(label=label, value=value, tool=tool, path=path)


def parse_answer(text: str) -> Answer | None:
    """The turn's final answer, or ``None`` when the text is not the answer object."""
    body = _strip_fence(text)
    if not body.startswith("{"):
        return None
    try:
        parsed = json.loads(body)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    summary = parsed.get("summary", "")
    claims = parsed.get("claims", [])
    if not isinstance(summary, str) or not isinstance(claims, list):
        return None
    resolved = tuple(claim for claim in (_claim(item) for item in claims) if claim is not None)
    if len(resolved) != len(claims):
        return None
    return Answer(summary=summary, claims=resolved)


__all__ = ["ANSWER_CONTRACT", "parse_answer"]
