"""Parity gate for the money functions exposed by the PyO3 bridge.

Every case in ``money_cases.jsonl`` is run against both the real Python original
(``ezchain_core.money`` / ``ezchain_adapters.quotes``) and the built Rust module. A case
matches only when the success value, or the exception's exact class, ``str``, ``code``
and ``message``, are identical. The Python implementation is the oracle; this test
never hard-codes expected values.

A string argument is the caller's encoding of an exact ``Decimal``: the oracle does
``Decimal(s)`` before calling the original, and the bridge parses the same string with
the proven Rust decimal kernel. Ints and typed refusals (float/bool/None) are handed to
both sides unchanged, so ``money.py``'s own guards are part of the comparison.

Build the module first (see the crate README), then run::

    PYTHONPATH=apps/api/src:packages/core-py/src:packages/adapters/src \\
      .venv/bin/python -m pytest rust/ezchain-bridge/tests/test_bridge_money_parity.py -v

Set ``EZCHAIN_BRIDGE_SO`` if the module lives elsewhere, and
``EZCHAIN_BRIDGE_MONEY_CASES`` to run a held-out corpus.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

CRATE = Path(__file__).resolve().parents[1]
ROOT = CRATE.parents[1]
sys.path[:0] = [
    str(ROOT / "apps" / "api" / "src"),
    str(ROOT / "packages" / "core-py" / "src"),
    str(ROOT / "packages" / "adapters" / "src"),
]

from ezchain_adapters import quotes  # noqa: E402
from ezchain_core import money  # noqa: E402
from ezchain_core.money import FloatMoneyError  # noqa: E402

CASES_PATH = Path(
    os.environ.get("EZCHAIN_BRIDGE_MONEY_CASES", CRATE / "tests" / "money_cases.jsonl")
)

# Pinned counts for the committed corpus; a held-out run with a different path skips them.
RECORDED_COUNTS = {
    "decimal_str": 69,
    "quantize_money": 65,
    "floor_money": 50,
    "to_minor_units": 48,
    "decimal_compare": 43,
    "to_raw": 37,
    "from_raw": 36,
    "from_minor_units": 32,
}
RECORDED_OUTCOMES = {
    "decimal_str": (65, 4),
    "decimal_compare": (43, 0),
    "quantize_money": (51, 14),
    "floor_money": (41, 9),
    "to_minor_units": (34, 14),
    "from_minor_units": (28, 4),
    "to_raw": (35, 2),
    "from_raw": (34, 2),
}
RECORDED_ERROR_CLASSES = {
    "InvalidOperation": 6,
    "MoneyError": 31,
    "FloatMoneyError": 3,
    "SubQuantumError": 5,
    "NotFound": 4,
}


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


def _decimal(value: Any) -> Any:
    """A string argument is the caller's exact Decimal spelling; other types pass through."""
    return Decimal(value) if isinstance(value, str) else value


def _compare(a: Any, b: Any) -> int:
    left, right = _decimal(a), _decimal(b)
    return (left > right) - (left < right)


# The real Python originals. Each takes the same raw args as the bridge function.
ORIGINALS: dict[str, Callable[..., Any]] = {
    "decimal_str": lambda value: str(Decimal(value)),
    "decimal_compare": _compare,
    "quantize_money": lambda value, field="amount": str(
        money.quantize_money(_decimal(value), field=field)
    ),
    "floor_money": lambda value, field="amount": str(
        money.floor_money(_decimal(value), field=field)
    ),
    "to_minor_units": lambda value, field="amount": money.to_minor_units(
        _decimal(value), field=field
    ),
    "from_minor_units": lambda minor: str(money.from_minor_units(minor)),
    "to_raw": lambda amount, decimals: quotes.to_raw(
        Decimal(amount) if isinstance(amount, str) else amount, decimals
    ),
    "from_raw": lambda raw, decimals: str(quotes.from_raw(raw, decimals)),
}


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


class BridgeMoneyParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge = _load_bridge()
        cls.cases = [
            json.loads(line) for line in CASES_PATH.read_text(encoding="utf-8").splitlines() if line
        ]

    def test_recorded_cases_match_python_originals(self) -> None:
        counts = {name: [0, 0] for name in ORIGINALS}  # [success, error]
        error_classes: dict[str, int] = {}

        for case in self.cases:
            name, args = case["fn"], case["args"]
            with self.subTest(fn=name, args=args):
                expected = _outcome(ORIGINALS[name], args)
                actual = _outcome(getattr(self.bridge, name), args)
                self.assertEqual(actual, expected)
            counts[name][0 if expected[0] == "success" else 1] += 1
            if expected[0] == "error":
                error_classes[expected[1].__name__] = error_classes.get(expected[1].__name__, 0) + 1

        totals = {name: (counts[name][0], counts[name][1]) for name in ORIGINALS}
        successes = sum(pair[0] for pair in totals.values())
        errors = sum(pair[1] for pair in totals.values())
        print(
            f"parity: {len(self.cases)}/{len(self.cases)} subtests match "
            f"({successes} success / {errors} error)"
        )
        for name in sorted(totals):
            print(f"  {name}: {totals[name][0]} success / {totals[name][1]} error")
        print(f"  error classes: {dict(sorted(error_classes.items()))}")

        if CASES_PATH == CRATE / "tests" / "money_cases.jsonl":
            self.assertEqual({k: v[0] + v[1] for k, v in totals.items()}, RECORDED_COUNTS)
            self.assertEqual(totals, RECORDED_OUTCOMES)
            self.assertEqual(error_classes, RECORDED_ERROR_CLASSES)

    def test_floats_are_refused_at_the_boundary(self) -> None:
        # money.py refuses floats with FloatMoneyError at its own boundary, so those
        # cases are parity-checked above. These two functions wrap pure adapter
        # helpers that do not guard floats themselves; the bridge must still refuse
        # them (fail closed, never accept a float for money).
        with self.subTest(fn="decimal_str"):
            with self.assertRaises(FloatMoneyError):
                self.bridge.decimal_str(0.5)
        with self.subTest(fn="to_raw"):
            with self.assertRaises(FloatMoneyError):
                self.bridge.to_raw(0.5, 8)


if __name__ == "__main__":
    unittest.main()
