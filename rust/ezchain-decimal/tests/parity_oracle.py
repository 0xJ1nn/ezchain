#!/usr/bin/env python3
"""Oracle harness for the ezchain-decimal parity prototype.

Generates a deterministic corpus, runs every case through the real Python
implementation in ``packages/core-py`` (``money.py`` under the venv) and records
its result as a fixture that ``tests/parity.rs`` asserts the Rust code matches.

Usage (from the checkout root, with the project venv active)::

    .venv/bin/python rust/ezchain-decimal/tests/parity_oracle.py            # write fixture
    .venv/bin/python rust/ezchain-decimal/tests/parity_oracle.py --check    # verify no drift

Every expected value is recorded as a token so the fixture is self-describing::

    V:<sign>|<coefficient digits>|<exponent>~<python str(Decimal)>   # decimal result
    I:<integer>                                                     # exact_precision
    C:<LT|EQ|GT>                                                    # comparison
    E:<ExceptionClassName>                                          # refusal

The Python implementation stays the oracle until the Rust gate passes; this
script writes its recorded answers, it does not read Rust output.
"""

from __future__ import annotations

import os
import random
import sys
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
CORE_PY_SRC = REPO_ROOT / "packages" / "core-py" / "src"
# Seed/output overrides exist so a maintainer can regenerate a held-out corpus
# (a different seed) to check the Rust type has not been overfit to one fixture.
SEED = int(os.environ.get("EZ_DECIMAL_SEED", "20261118"))
FIXTURE = Path(os.environ.get("EZ_DECIMAL_FIXTURE", str(HERE / "parity_fixture.txt")))
sys.path.insert(0, str(CORE_PY_SRC))

from ezchain_core.money import (  # noqa: E402
    exact_precision,
    exact_product,
    exact_sum,
    floor_money,
    quantize_money,
)

# A fixed seed makes the corpus reproducible in CI and in a rerun.
SEED = 20261118
BIG = "12345678901234567890123456789012345678"  # 38 digits, the NUMERIC(38,8) column


def dec_token(value: Decimal) -> str:
    """Encode a Decimal's exact triple plus its str form as one token."""
    tup = value.as_tuple()
    digits = "".join(str(d) for d in tup.digits) if tup.digits else "0"
    return f"V:{tup.sign}|{digits}|{tup.exponent}~{str(value)}"


def rnd_dec(rng: random.Random, max_digits: int = 40, exp_lo: int = -12, exp_hi: int = 6) -> str:
    """A finite decimal literal with arbitrary precision and a random exponent."""
    length = rng.randint(1, max_digits)
    coefficient = rng.choice("123456789") + "".join(
        rng.choice("0123456789") for _ in range(length - 1)
    )
    exponent = rng.randint(exp_lo, exp_hi)
    sign = rng.choice(["", "", "", "-"])
    notation = rng.choice(["e", "E"])
    return f"{sign}{coefficient}{notation}{exponent:+d}"


def tricky_corpus() -> list[tuple[str, list[str]]]:
    """Hand-picked cases named in the field brief plus boundary conditions."""
    cases: list[tuple[str, list[str]]] = [
        # 0.1 + 0.2 and representation-insensitive comparisons.
        ("sum", ["0.1", "0.2"]),
        ("cmp", ["0.1", "0.10"]),
        ("cmp", ["-0", "0"]),
        ("cmp", ["-0.0", "0"]),
        ("cmp", ["1E+3", "1000"]),
        ("cmp", ["-2.5", "0.1"]),
        ("cmp", [BIG, BIG]),
        # 38-digit column values and products/sums that would round at 28 digits.
        ("q", [BIG]),
        ("f", [BIG]),
        ("prod", [BIG, "0.00000001"]),
        ("sum", [BIG, "0.1"]),
        ("ep", ["8", BIG]),
        ("prod", ["12345678901234567890", "12345678901234567890"]),
        ("sum", [BIG, "0.00000001", "0.1", "-0.2"]),
        # Negative exponents and tiny values.
        ("q", ["1E-10"]),
        ("f", ["1E-10"]),
        ("q", ["5e-9"]),
        ("q", ["15e-9"]),
        ("q", ["25e-9"]),
        ("q", ["0.000000035"]),
        ("f", ["0.000000009"]),
        ("f", ["-0.000000009"]),
        ("prod", ["0.1", "0.2"]),
        ("prod", ["1.0", "2.0"]),
        ("prod", ["0.0", "-2"]),
        ("prod", ["-0", "3"]),
        # Half-even ties, both directions, and the neighbouring non-ties.
        ("q", ["1.005"]),
        ("q", ["2.675"]),
        ("q", ["1.234567845"]),
        ("q", ["1.234567855"]),
        ("q", ["1.234567895"]),
        ("q", ["-1.234567895"]),
        ("q", ["-0.000000005"]),
        ("q", ["-0.000000015"]),
        ("f", ["1.234567895"]),
        ("f", ["-1.234567895"]),
        ("f", ["-2.5"]),
        # Zero-scale handling: the sum exponent is the minimum operand exponent.
        ("sum", ["1", "0.00"]),
        ("sum", ["0.000", "1"]),
        ("sum", ["-0", "0"]),
        ("sum", ["0.00", "-0.00"]),
        ("sum", ["1E+3"]),
        ("sum", ["1E+3", "0.1"]),
        ("sum", []),
        ("q", ["1E+3"]),
        ("q", ["-0"]),
        ("q", ["0E+5"]),
        ("parse", ["0E+5"]),
        ("parse", ["000.100"]),
        ("parse", ["-0.0"]),
        # Non-finite rejection at every money boundary.
        *[("q", [s]) for s in ("NaN", "sNaN", "Infinity", "-Infinity")],
        *[("f", [s]) for s in ("NaN", "Infinity")],
        *[("sum", [s, "0.1"]) for s in ("NaN", "Infinity")],
        ("prod", ["1", "sNaN"]),
        ("ep", ["8", "NaN"]),
    ]
    return cases


def random_corpus() -> list[tuple[str, list[str]]]:
    """Deterministic seeded cases across every operation, up to 40 digits."""
    rng = random.Random(SEED)
    plan = {"parse": 60, "ep": 40, "q": 80, "f": 80, "sum": 70, "prod": 50, "cmp": 20}
    cases: list[tuple[str, list[str]]] = []
    for op, count in plan.items():
        for _ in range(count):
            if op == "parse":
                args = [rnd_dec(rng)]
            elif op == "ep":
                extra = str(rng.randint(0, 12))
                args = [extra, *(rnd_dec(rng) for _ in range(rng.randint(1, 3)))]
            elif op in ("q", "f"):
                args = [rnd_dec(rng, exp_lo=-12, exp_hi=3)]
            elif op == "sum":
                args = [rnd_dec(rng) for _ in range(rng.randint(0, 6))]
            elif op == "prod":
                args = [rnd_dec(rng) for _ in range(rng.randint(1, 4))]
            else:  # cmp
                args = [rnd_dec(rng), rnd_dec(rng)]
            cases.append((op, args))
    return cases


def expected_for(op: str, args: list[str]) -> str:
    """Run one case through the Python oracle and encode its answer."""
    if op == "parse":
        return dec_token(Decimal(args[0]))

    try:
        if op == "ep":
            extra = int(args[0])
            values = [Decimal(s) for s in args[1:]]
            return f"I:{exact_precision(*values, extra=extra)}"
        if op == "q":
            return dec_token(quantize_money(Decimal(args[0])))
        if op == "f":
            return dec_token(floor_money(Decimal(args[0])))
        if op == "sum":
            return dec_token(exact_sum(Decimal(s) for s in args))
        if op == "prod":
            return dec_token(exact_product(*(Decimal(s) for s in args)))
    except Exception as exc:  # noqa: BLE001 - the code name is the recorded answer
        return f"E:{type(exc).__name__}"

    if op == "cmp":
        a, b = Decimal(args[0]), Decimal(args[1])
        if a < b:
            return "C:LT"
        if a > b:
            return "C:GT"
        return "C:EQ"
    raise AssertionError(f"unknown operation {op!r}")


def render(cases: list[tuple[str, list[str]]]) -> str:
    lines = []
    for op, args in cases:
        fields = [op, *args, expected_for(op, args)]
        lines.append("\t".join(fields))
    return "\n".join(lines) + "\n"


def main() -> int:
    cases = tricky_corpus() + random_corpus()
    content = render(cases)
    if "--check" in sys.argv:
        current = FIXTURE.read_text(encoding="utf-8") if FIXTURE.exists() else ""
        if current != content:
            print(f"FAIL: fixture drift ({len(current.splitlines())} stored vs {len(cases)} generated)")
            return 1
        print(f"OK: fixture matches oracle ({len(cases)} cases)")
        return 0
    FIXTURE.write_text(content, encoding="utf-8")
    by_op: dict[str, int] = {}
    for op, _ in cases:
        by_op[op] = by_op.get(op, 0) + 1
    print(f"wrote {FIXTURE} with {len(cases)} cases")
    print("per operation: " + ", ".join(f"{k}={v}" for k, v in sorted(by_op.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
