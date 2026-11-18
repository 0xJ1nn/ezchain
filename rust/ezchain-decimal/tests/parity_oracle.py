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
    N:<signed integer>                                              # to_minor_units
    P:<fill>;<worst|None>;<slip|None>;<venue_leg>;<platform>        # preview_cost sheet
    E:<ExceptionClassName>                                          # refusal

Boundary-input tokens (for the ``ad`` / ``fmg`` type-guard ops) preserve the
Python type that reached ``_as_decimal`` / ``from_minor_units``::

    D:<literal>     Decimal(literal)       I:<int>     int(literal)
    F:<literal>     float(literal)         B:True|False bool
    S:<text>        str (any other type)   N:          None
    X:<spelling>    a non-finite Decimal (NaN/sNaN/±Infinity)

The Python implementation stays the oracle until the Rust gate passes; this
script writes its recorded answers, it does not read Rust output.
"""

from __future__ import annotations

import os
import random
import sys
from datetime import UTC, datetime
from decimal import Decimal, localcontext
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
    _as_decimal,
    bps_fee,
    exact_neg,
    exact_precision,
    exact_product,
    exact_sum,
    floor_money,
    from_minor_units,
    preview_cost,
    quantize_money,
    require_money,
    require_quantity,
    to_minor_units,
)
from ezchain_core.types import SourceRef  # noqa: E402

# SEED comes from the environment so a held-out corpus with a different seed can
# be generated without overwriting the committed fixture (see the crate README).
BIG = "12345678901234567890123456789012345678"  # 38 digits, the NUMERIC(38,8) column
# preview_cost's source is a pure echo; any valid SourceRef serves the oracle.
SOURCE = SourceRef(name="parity-oracle", url=None, observed_at=datetime.now(UTC))


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


def rnd_whole(rng: random.Random) -> str:
    """A whole number of minor units: exponent >= -8, so 1e-8 scaling is exact."""
    return rnd_dec(rng, exp_lo=-8, exp_hi=6)


def rnd_minor(rng: random.Random) -> str:
    """A signed integer count of minor units (up to 40 digits)."""
    length = rng.randint(1, 40)
    digits = rng.choice("123456789") + "".join(rng.choice("0123456789") for _ in range(length - 1))
    return rng.choice(["", "-"]) + digits


NON_FINITE = {"NaN", "sNaN", "Infinity", "-Infinity"}


def parse_input(token: str) -> object:
    """Decode a boundary-input token into a real Python value of that type."""
    tag, _, payload = token.partition(":")
    if tag == "D":
        return Decimal(payload)
    if tag == "I":
        return int(payload)
    if tag == "F":
        return float(payload)
    if tag == "B":
        return payload == "True"
    if tag == "S":
        return payload
    if tag == "N":
        return None
    if tag == "X" and payload in NON_FINITE:
        return Decimal(payload)
    raise AssertionError(f"unknown boundary input {token!r}")


def rnd_input(rng: random.Random) -> str:
    """A deterministic boundary-input token covering every Python input type."""
    tag = rng.choice(["D", "I", "F", "B", "S", "N", "X"])
    if tag == "D":
        return "D:" + rnd_dec(rng)
    if tag == "I":
        return "I:" + rnd_minor(rng)
    if tag == "F":
        return "F:" + repr(rng.choice([0.0, 0.1, 1.5, -2.5, 1e30, -1e-30]))
    if tag == "B":
        return "B:" + rng.choice(["True", "False"])
    if tag == "S":
        return "S:" + rng.choice(["1.5", "", "0", "42"])
    if tag == "N":
        return "N:"
    return "X:" + rng.choice(sorted(NON_FINITE))


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
        # Division: exact quotients, preferred exponents, half-even ties, signs,
        # non-terminating expansions and power-of-ten divisors.
        ("div", ["28", "1", "8"]),
        ("div", ["28", "1.00", "2"]),
        ("div", ["28", "1.20", "2"]),
        ("div", ["28", "100", "10000"]),
        ("div", ["28", "12345", "10000"]),
        ("div", ["28", "1", "10000"]),
        ("div", ["4", "100000", "3"]),
        ("div", ["4", "101", "1000"]),
        ("div", ["2", "10", "4"]),
        ("div", ["2", "1", "80"]),
        ("div", ["3", "999", "2"]),
        ("div", ["28", "1", "3"]),
        ("div", ["28", "2", "3"]),
        ("div", ["28", "1", "7"]),
        ("div", ["28", "1E-8", "3"]),
        ("div", ["28", "0E+5", "5"]),
        ("div", ["28", "-1", "8"]),
        ("div", ["28", "1", "-8"]),
        ("div", ["28", "-1", "-3"]),
        ("div", ["28", BIG, "9950"]),
        ("div", ["28", "1", "0"]),
        # bps_fee: the fee the journal books for a notional at a given bps.
        ("bps", ["100", "50"]),
        ("bps", ["0.1", "25"]),
        ("bps", ["-12.345678901", "50"]),
        ("bps", [BIG + ".12345678", "50"]),
        ("bps", ["100", "0"]),
        ("bps", ["1", "3"]),
        ("bps", ["100", "-1"]),
        ("bps", ["NaN", "50"]),
        # preview_cost: paper mark vs executable, venue-leg max, slippage window.
        ("pc", ["paper_mark", "100", "999", "None", "50"]),
        ("pc", ["jupiter", "100", "0.123456789", "10", "50"]),
        ("pc", ["jupiter", "100", "2.5", "10", "50"]),
        ("pc", ["jupiter", "100", "0.000000001", "10000", "50"]),
        ("pc", ["paper_mark", "100", "999", "None", "50"]),
        ("pc", ["jupiter", "0.1", "0.01", BIG, "25"]),
        ("pc", ["jupiter", "100", "1", "None", "50"]),
        ("pc", ["jupiter", "0", "1", "None", "50"]),
        ("pc", ["jupiter", "100", "NaN", "10", "50"]),
        ("pc", ["jupiter", "100", "1", "10", "-1"]),
        # require_money: representation is preserved; sub-minor values and
        # non-finite spellings are refused at the boundary.
        ("rm", ["1.5"]),
        ("rm", ["1.500"]),
        ("rm", ["-0.00000000"]),
        ("rm", ["0E+5"]),
        ("rm", [BIG + ".12345678"]),
        ("rm", ["0.000000010"]),
        ("rm", ["0.1"]),
        ("rm", ["1.000000001"]),
        ("rm", ["NaN"]),
        ("rm", ["Infinity"]),
        ("rm", ["sNaN"]),
        # require_quantity: finite exact values pass without a scale limit.
        ("rq", ["1.2345678901"]),
        ("rq", ["-0"]),
        ("rq", ["1E+3"]),
        ("rq", ["0.000000010"]),
        ("rq", [BIG]),
        ("rq", ["NaN"]),
        ("rq", ["-Infinity"]),
        # to_minor_units: exact scaling, sign of zero, and both refusal codes.
        ("tmu", ["1.5"]),
        ("tmu", ["-2.5"]),
        ("tmu", ["0"]),
        ("tmu", ["-0"]),
        ("tmu", ["1E+3"]),
        ("tmu", ["0.000000010"]),
        ("tmu", [BIG + ".12345678"]),
        ("tmu", ["-" + BIG + ".12345678"]),
        ("tmu", ["0.1"]),
        ("tmu", ["0.000000009"]),
        ("tmu", ["NaN"]),
        ("tmu", ["Infinity"]),
        # from_minor_units: inverse conversion for positive, negative, zero and
        # arbitrarily large integer counts.
        ("fmu", ["150000000"]),
        ("fmu", ["-250000000"]),
        ("fmu", ["0"]),
        ("fmu", ["1"]),
        ("fmu", ["-5"]),
        ("fmu", ["100000000000"]),
        ("fmu", ["123456789012345678901234567890"]),
        ("fmu", ["-123456789012345678901234567890"]),
        # exact_neg: flips the sign on the digits, including the sign of zero,
        # with no ambient-context rounding of a long coefficient.
        ("neg", ["1.5"]),
        ("neg", ["-2.5"]),
        ("neg", ["0"]),
        ("neg", ["-0"]),
        ("neg", ["-0.0"]),
        ("neg", ["-0E-8"]),
        ("neg", ["1E+3"]),
        ("neg", ["0.000000010"]),
        ("neg", [BIG + ".12345678"]),
        ("neg", ["12345678901234567890123456789"]),
        # Python input-type guards at _as_decimal and from_minor_units.
        ("ad", ["D:1.5"]),
        ("ad", ["I:42"]),
        ("ad", ["I:123456789012345678901234567890"]),
        ("ad", ["I:-7"]),
        ("ad", ["B:True"]),
        ("ad", ["B:False"]),
        ("ad", ["F:0.1"]),
        ("ad", ["F:-0.0"]),
        ("ad", ["F:1e30"]),
        ("ad", ["S:1.5"]),
        ("ad", ["S:"]),
        ("ad", ["N:"]),
        *[("ad", [f"X:{s}"]) for s in sorted(NON_FINITE)],
        ("fmg", ["I:150000000"]),
        ("fmg", ["I:-250000000"]),
        ("fmg", ["I:0"]),
        ("fmg", ["B:True"]),
        ("fmg", ["B:False"]),
        ("fmg", ["F:1.0"]),
        ("fmg", ["S:1"]),
        ("fmg", ["N:"]),
        ("fmg", ["D:1"]),
        # Ambient-context boundaries: Emax overflow / InvalidOperation and the
        # MAX_PREC ValueError, reached through money.py's own localcontext().
        ("q", ["1E+1000000"]),
        ("f", ["1E+1000000"]),
        ("q", ["1E-1000000"]),
        ("q", ["1E+9999999999"]),
        ("f", ["1E+9999999999"]),
        ("q", ["1E-9999999999"]),
        ("q", ["1E+999999999999999999"]),
        ("tmu", ["1E+999999"]),
        ("tmu", ["1E+1000000"]),
        ("tmu", ["1E+9999999999"]),
        ("tmu", ["1E+999999999999999999"]),
        ("tmu", ["1E-999999"]),
        ("prod", ["1E+999999", "1E+999999"]),
        ("prod", ["1E+9999999999", "1"]),
        ("prod", ["1E+999999999999999999", "1"]),
        ("ep", ["8", "1E+9999999999"]),
        ("ep", ["8", "1E+999999999999999999"]),
        ("parse", ["1E+9999999999"]),
        ("parse", ["1E-9999999999"]),
        ("parse", ["1E+9999999999999999999"]),
    ]
    return cases


def random_corpus() -> list[tuple[str, list[str]]]:
    """Deterministic seeded cases across every operation, up to 40 digits."""
    rng = random.Random(SEED)
    plan = {
        "parse": 60,
        "ep": 40,
        "q": 80,
        "f": 80,
        "sum": 70,
        "prod": 50,
        "cmp": 20,
        "div": 70,
        "bps": 45,
        "pc": 45,
        "rm": 50,
        "rq": 45,
        "tmu": 55,
        "fmu": 55,
        "neg": 50,
        "ad": 45,
        "fmg": 35,
    }
    fee_choices = [0, 1, 5, 25, 30, 50, 99, 300, 2500, 9999, 10000]
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
            elif op == "div":
                precision = str(rng.choice([2, 4, 8, 12, 28, 60]))
                args = [precision, rnd_dec(rng), rnd_dec(rng)]
            elif op == "bps":
                fee = rng.choice(fee_choices)
                if rng.random() < 0.05:  # exercise the negative-fee refusal
                    fee = -fee
                args = [rnd_dec(rng), str(fee)]
            elif op == "pc":
                kind = rng.choice(["paper_mark", "jupiter", "kyberswap"])
                notional = rnd_dec(rng).lstrip("-") or "1"
                venue = rnd_dec(rng)
                amount = "None" if rng.random() < 0.3 else rnd_dec(rng)
                args = [kind, notional, venue, amount, str(rng.choice(fee_choices))]
            elif op in ("rm", "tmu"):
                # Mix whole and sub-minor magnitudes so both the success path
                # and SubQuantumError are exercised in the deterministic corpus.
                args = [rnd_whole(rng) if rng.random() < 0.5 else rnd_dec(rng)]
            elif op in ("rq", "neg"):
                args = [rnd_dec(rng)]
            elif op == "fmu":
                args = [rnd_minor(rng)]
            elif op in ("ad", "fmg"):
                args = [rnd_input(rng)]
            else:  # cmp
                args = [rnd_dec(rng), rnd_dec(rng)]
            cases.append((op, args))
    return cases


def pc_token(preview, venue_leg: Decimal) -> str:
    """Encode the computed numeric fields of a CostPreview as one token."""
    worst = "None" if preview.worst_case_out is None else dec_token(preview.worst_case_out)
    slip = "None" if preview.slippage_bps is None else str(preview.slippage_bps)
    return (
        f"P:{dec_token(preview.fill_fee_usd)};{worst};{slip};"
        f"{dec_token(venue_leg)};{dec_token(preview.platform_fee_usd)}"
    )


def expected_for(op: str, args: list[str]) -> str:
    """Run one case through the Python oracle and encode its answer."""
    try:
        if op == "parse":
            return dec_token(Decimal(args[0]))
        if op == "ad":
            return dec_token(_as_decimal(parse_input(args[0]), field="x"))
        if op == "fmg":
            return dec_token(from_minor_units(parse_input(args[0])))
        if op == "div":
            precision = int(args[0])
            with localcontext() as context:
                context.prec = precision
                return dec_token(Decimal(args[1]) / Decimal(args[2]))
        if op == "bps":
            return dec_token(bps_fee(Decimal(args[0]), int(args[1])))
        if op == "pc":
            kind, notional, venue, amount, fee = args
            preview = preview_cost(
                kind=kind,
                notional_usd=Decimal(notional),
                venue_fee_usd=Decimal(venue),
                price_impact_pct=None,
                amount_out=None if amount == "None" else Decimal(amount),
                fee_bps=int(fee),
                source=SOURCE,
            )
            return pc_token(preview, quantize_money(Decimal(venue)))
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
        if op == "rm":
            return dec_token(require_money(Decimal(args[0])))
        if op == "rq":
            return dec_token(require_quantity(Decimal(args[0])))
        if op == "tmu":
            return f"N:{to_minor_units(Decimal(args[0]))}"
        if op == "fmu":
            return dec_token(from_minor_units(int(args[0])))
        if op == "neg":
            return dec_token(exact_neg(Decimal(args[0])))
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
