"""Typed client for the optional in-process PyO3 bridge (ADR-0052 integration contract).

The compiled ``ezchain_bridge`` extension is optional. When it is importable the two
ported kernels run in Rust; otherwise the Python originals are used unchanged. The
fallback is silent in behaviour but visible through :func:`using_rust`, so a test or
an operator can tell which implementation is serving a call without changing any
result or exception.

This module lives in ``ezchain_core`` (the lowest Python layer) on purpose. The
``stale_reason`` caller is inside ``ezchain_core.freshness`` and cannot import upward
into ``apps/api`` without a cycle; ``apps/api`` already depends on ``ezchain_core``.
The one deferred import of the ``normalize_scopes`` original below happens only on the
fallback path and only from an ``apps/api`` caller, where that module is already loaded.

No exception is caught around a bridge call: the Rust path raises the original
``ApiKeyError`` class, and the Python path raises the same class from the oracle, so
callers see one contract either way.
"""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from typing import Any, cast

_BRIDGE_MODULE = "ezchain_bridge"


def _load_bridge() -> Any:
    """Try to import the compiled extension once. Return ``None`` if it is absent."""
    try:
        return importlib.import_module(_BRIDGE_MODULE)
    except Exception:  # noqa: BLE001 - any load failure means the Python original is used
        return None


_BRIDGE: Any = _load_bridge()


def using_rust() -> bool:
    """Whether the compiled bridge loaded and will serve the typed wrappers."""
    return _BRIDGE is not None


def normalize_scopes(scopes: Sequence[str] | None) -> tuple[str, ...]:
    """Normalise a scope grant, raising ``ApiKeyError`` on refusal (unchanged contract)."""
    if _BRIDGE is not None:
        return cast(tuple[str, ...], _BRIDGE.normalize_scopes(scopes))
    from ezchain_api.services.api_keys import normalize_scopes as python_normalize_scopes

    return python_normalize_scopes(scopes)


def stale_reason(source_class: str) -> str:
    """The stable reason code a stale read of ``source_class`` reports."""
    if _BRIDGE is not None:
        return cast(str, _BRIDGE.stale_reason(source_class))
    from ezchain_core.freshness import stale_reason as python_stale_reason

    return python_stale_reason(source_class)


__all__ = ["normalize_scopes", "stale_reason", "using_rust"]
