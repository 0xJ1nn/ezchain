"""The one canonical encoding, the digest functions built on it, and the error taxonomy.

Two different records must never share a digest, so the encoding is one JSON object with
sorted keys over a validated type domain rather than a delimiter join: the ledger seal review
found that a ``"|"`` join let two different tapes hash the same.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any


class TraceError(RuntimeError):
    """Base for every trace failure."""


class TraceEncodingError(TraceError):
    """A value has no canonical form, so hashing it would be a guess."""


class TraceStoreError(TraceError):
    """The store refused the append or could not read what it was given."""


class TraceWriteFailed(TraceError):
    """The recorder could not persist an event; the session must not proceed."""


class TraceIncomplete(TraceError):
    """The events do not describe a finished session."""


class TraceChainBroken(TraceError):
    """The chain does not verify at one event."""

    def __init__(self, index: int, reason: str, detail: str) -> None:
        super().__init__(f"trace event {index} fails verification ({reason}): {detail}")
        self.index = index
        self.reason = reason


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _refuse_unencodable(value: Any, path: str) -> None:
    if isinstance(value, float | Decimal):
        raise TraceEncodingError(
            f"{path}: {type(value).__name__} has no canonical form; format it as a string first"
        )
    if isinstance(value, tuple):
        raise TraceEncodingError(
            f"{path}: a tuple has no canonical form; use a list so [] means exactly one thing"
        )


def _check(value: Any, path: str) -> Any:
    if value is None or isinstance(value, bool | int | str):
        return value
    _refuse_unencodable(value, path)
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TraceEncodingError(f"{path}: a field name must be a string, not {key!r}")
            out[key] = _check(item, f"{path}.{key}")
        return out
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [_check(item, f"{path}[{index}]") for index, item in enumerate(value)]
    raise TraceEncodingError(f"{path}: {type(value).__name__} has no canonical form")


def canonical(record: Mapping[str, Any]) -> str:
    """One canonical encoding, so two different records cannot share a digest.

    JSON over a validated, JSON-native type domain with sorted keys and no whitespace. The
    encoding is injective on that domain: no separator exists to smuggle a field boundary
    across, a string and a number of the same text stay distinct, and anything whose encoding
    would depend on a platform detail (a float, a ``Decimal``) or silently change type (a
    tuple, which JSON would render as a list) is refused.
    """
    return json.dumps(
        _check(record, "record"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def digest_text(text: str) -> str:
    return _sha(text)


def digest_json(value: Any) -> str:
    """A digest of arbitrary tool data, never a copy of it.

    Tool arguments and results are JSON-RPC data, so they are already JSON-native; anything
    else is refused rather than stringified, because ``str()`` would let ``Decimal("1")`` and
    the string ``"1"`` share a digest. An integer and a float encode differently (``1`` vs
    ``1.0``), a tuple is refused rather than rendered as a list, and an object whose values
    contain a delimiter cannot collide with a sibling.
    """
    try:
        encoded = json.dumps(
            _tool_data(value, "data"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise TraceEncodingError(f"tool data has no canonical form: {exc}") from exc
    return _sha(encoded)


def _tool_data(value: Any, path: str) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TraceEncodingError(f"{path}: a JSON object key must be a string, not {key!r}")
            out[key] = _tool_data(item, f"{path}.{key}")
        return out
    if isinstance(value, tuple):
        raise TraceEncodingError(f"{path}: a tuple has no canonical form; use a list")
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [_tool_data(item, f"{path}[{index}]") for index, item in enumerate(value)]
    raise TraceEncodingError(f"{path}: {type(value).__name__} has no canonical form")


def money(value: Decimal) -> str:
    """A Decimal as the one string that means it, e.g. ``Decimal("0.020")`` → ``"0.02"``."""
    if not isinstance(value, Decimal):
        raise TraceEncodingError("money must be a Decimal")
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


__all__ = [
    "TraceChainBroken",
    "TraceEncodingError",
    "TraceError",
    "TraceIncomplete",
    "TraceStoreError",
    "TraceWriteFailed",
    "canonical",
    "digest_json",
    "digest_text",
    "money",
]
