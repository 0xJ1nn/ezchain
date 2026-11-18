"""Typed client for the optional in-process PyO3 bridge (ADR-0052 integration contract).

The compiled ``ezchain_bridge`` extension is optional. When it is importable the
ported ``normalize_scopes`` kernel runs in Rust; otherwise the Python original is
used unchanged. The fallback is silent in behaviour but visible through
:func:`using_rust`, so a test or an operator can tell which implementation served a
call without a config flag or telemetry.

The client lives in ``apps/api`` rather than ``packages/core-py``: the fallback calls
the Python original in this same layer, and a core-py module must never import the
application layer. The import of that original is deferred to the fallback branch so
there is no cycle with ``api_keys`` at module load.

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
    """Whether the compiled bridge loaded and will serve the typed wrapper."""
    return _BRIDGE is not None


def normalize_scopes(scopes: Sequence[str] | None) -> tuple[str, ...]:
    """Normalise a scope grant, raising ``ApiKeyError`` on refusal (unchanged contract)."""
    if _BRIDGE is not None:
        return cast(tuple[str, ...], _BRIDGE.normalize_scopes(scopes))
    from ezchain_api.services.api_keys import normalize_scopes as python_normalize_scopes

    return python_normalize_scopes(scopes)


__all__ = ["normalize_scopes", "using_rust"]
