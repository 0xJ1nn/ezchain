"""Parity gate for the in-process PyO3 bridge.

Every case in ``cases.jsonl`` is run against both the Python original and the built
Rust module. A case matches only when the success value, or the exception's exact
class, ``str``, ``code`` and ``message``, are identical. The Python implementation is
the oracle; this test never hard-codes expected values.

Build the module first (see the crate README), then run::

    .venv/bin/python -m pytest rust/ezchain-bridge/tests/test_bridge_parity.py -v

The module path can be supplied with ``EZCHAIN_BRIDGE_SO`` when it is built elsewhere.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from typing import Any, Callable

CRATE = Path(__file__).resolve().parents[1]
ROOT = CRATE.parents[1]
sys.path[:0] = [str(ROOT / "apps" / "api" / "src"), str(ROOT / "packages" / "core-py" / "src")]

from ezchain_api.services.api_keys import ApiKeyError  # noqa: E402
from ezchain_api.services.api_keys import normalize_scopes as python_normalize_scopes
from ezchain_core.freshness import stale_reason as python_stale_reason

CASES_PATH = CRATE / "tests" / "cases.jsonl"
RECORDED_COUNTS = {"stale_reason": 10, "normalize_scopes": 53}
SCOPE_OUTCOME_COUNTS = {"success": 7, "error": 46}


def _load_bridge() -> Any:
    """Import the built extension module, preferring an explicit path."""
    candidates = []
    explicit = os.environ.get("EZCHAIN_BRIDGE_SO")
    if explicit:
        candidates.append(Path(explicit))
    for profile in ("debug", "release"):
        target = CRATE.parent / "target" / profile
        candidates.append(target / "ezchain_bridge.so")
        candidates.append(target / "libezchain_bridge.so")
        candidates.append(target / "libezchain_bridge.dylib")
    for path in candidates:
        if path.is_file():
            spec = importlib.util.spec_from_file_location("ezchain_bridge", path)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules["ezchain_bridge"] = module
            spec.loader.exec_module(module)
            return module
    raise AssertionError(
        "ezchain-bridge extension not built; set EZCHAIN_BRIDGE_SO or build into rust/target"
    )


def _outcome(function: Callable[..., Any], args: list[Any]) -> tuple[Any, ...]:
    """A comparable record of a call: its value, or its exact typed error."""
    try:
        return ("success", function(*args))
    except Exception as exc:  # noqa: BLE001 - the recorded cases include typed errors
        return (
            "error",
            type(exc),
            str(exc),
            getattr(exc, "code", None),
            getattr(exc, "message", None),
        )


class BridgeParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge = _load_bridge()
        cls.cases = [
            json.loads(line) for line in CASES_PATH.read_text(encoding="utf-8").splitlines() if line
        ]

    def test_recorded_cases_match_python_originals(self) -> None:
        originals: dict[str, Callable[..., Any]] = {
            "stale_reason": python_stale_reason,
            "normalize_scopes": python_normalize_scopes,
        }
        bridged: dict[str, Callable[..., Any]] = {
            "stale_reason": self.bridge.stale_reason,
            "normalize_scopes": self.bridge.normalize_scopes,
        }
        counts = {name: 0 for name in RECORDED_COUNTS}
        scope_outcomes = {"success": 0, "error": 0}

        for case in self.cases:
            name, args = case["fn"], case["args"]
            with self.subTest(fn=name, args=args):
                expected = _outcome(originals[name], args)
                actual = _outcome(bridged[name], args)
                self.assertEqual(actual, expected)
            counts[name] += 1
            if name == "normalize_scopes":
                scope_outcomes[expected[0]] += 1

        self.assertEqual(counts, RECORDED_COUNTS)
        self.assertEqual(scope_outcomes, SCOPE_OUTCOME_COUNTS)
        print(
            f"parity: {len(self.cases)}/{len(self.cases)} cases match "
            f"(stale_reason {counts['stale_reason']}, "
            f"normalize_scopes {counts['normalize_scopes']}: "
            f"{scope_outcomes['success']} success / {scope_outcomes['error']} error)"
        )

    def test_scope_errors_are_the_original_exception_class(self) -> None:
        with self.assertRaises(ApiKeyError) as original:
            python_normalize_scopes(["bogus", "read"])
        with self.assertRaises(ApiKeyError) as bridged_error:
            self.bridge.normalize_scopes(["bogus", "read"])
        self.assertIs(type(bridged_error.exception), type(original.exception))
        self.assertEqual(bridged_error.exception.code, original.exception.code)
        self.assertEqual(bridged_error.exception.message, original.exception.message)


if __name__ == "__main__":
    unittest.main()
