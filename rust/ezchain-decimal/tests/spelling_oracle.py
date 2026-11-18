#!/usr/bin/env python3
"""Focused oracle for the *spellings* `decimal.Decimal(str)` accepts.

The main parity fixture uses canonical spellings only, so it cannot catch a
parser that rejects an alternative spelling of the same value. This harness
records the exact accepted/rejected behaviour of the real Python oracle for
underscores, Unicode Nd digits, signs, exponents, whitespace and non-finite
words. ``tests/spelling.rs`` asserts the Rust parser matches every record.

Usage (from the checkout root, with the project venv active)::

    .venv/bin/python rust/ezchain-decimal/tests/spelling_oracle.py            # write fixture
    .venv/bin/python rust/ezchain-decimal/tests/spelling_oracle.py --check    # verify no drift

Each fixture line is ``<hex-utf8>\\t<ascii label>\\t<expected>``. Hex keeps
literal tabs/newlines and non-ASCII digits unambiguous without a JSON parser on
the Rust side; the label is for human diagnostics only. Expected tokens:

    V:<sign>|<coefficient>|<exponent>~<str(Decimal)>   accepted finite value
    E:NonFinite                                       Decimal() accepts a non-finite
                                                      word, which money.py refuses
    E:ConversionSyntax                                InvalidOperation, ConversionSyntax
    E:InvalidOperation                                InvalidOperation at construction
                                                      (e.g. an exponent beyond MAX_EMAX)

The Python implementation stays the oracle; this script never reads Rust output.
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = Path(os.environ.get("EZ_DECIMAL_SPELLING_FIXTURE", str(HERE / "spelling_fixture.txt")))


def spelling_cases() -> list[str]:
    """Curated spellings; every expectation is produced by the oracle below."""
    return [
        # Underscores — CPython ignores them anywhere, not just between digits.
        "1_0",
        "1_000.25",
        "1_0.0_1",
        "1_0e1_0",
        "1_2.3_4e5_6",
        "0_0.0_0",
        "000_100",
        "_1_",
        "_10",
        "10_",
        "1__0",
        "1_.0",
        "1._0",
        "1.0_",
        "1_.",
        "._1",
        "1_e2",
        "1e_2",
        "1e2_",
        "1_e_2",
        "1e+_2",
        "1e-_2",
        "1_0._1",
        "1_.0_1",
        "1._0_1",
        "+_1",
        "-_1",
        "_",
        "__",
        "-__",
        "_e3",
        "._",
        # Nd block U+0660..U+0669 (Arabic-Indic).
        "\u0661\u0662\u0663",
        "\u0661\u0662.\u0663\u0664",
        "-\u0661\u0662",
        "\u0661\u0662e\u0663",
        "\u0661_\u0662",
        "\u0661\u0662e+\u0663_\u0664",
        "\u0660",
        "\u0669",
        # Nd block U+06F0..U+06F9 (Extended Arabic-Indic).
        "\u06f1\u06f2\u06f3",
        "\u06f1\u06f2.\u06f3\u06f4",
        "-\u06f1\u06f2",
        "\u06f1\u06f2e\u06f3",
        # Nd block U+0966..U+096F (Devanagari).
        "\u0967\u0968\u0969",
        "\u0967\u0968.\u0969\u096a",
        "-\u0967\u0968",
        "\u0967\u0968e\u0969",
        "\u0966",
        # Nd block U+FF10..U+FF19 (Fullwidth).
        "\uff11\uff12\uff13",
        "\uff11\uff12.\uff13\uff14",
        "-\uff11\uff12",
        "\uff11\uff12e\uff13",
        "\uff10",
        # Mixed scripts and Nd digits in the exponent.
        "1\u06623",
        "\u0661\u06f2",
        "\u0661\uff12",
        "1e\u0663",
        "1e+\uff11",
        "1e\u0966",
        "1e1_\u0662",
        "1_\u0662.3_4e5_6",
        # Signs: only ASCII + and - are accepted.
        "+1",
        "-1",
        " +1 ",
        "--1",
        "++1",
        "\uff0d1",  # fullwidth minus sign
        "\u22121",  # mathematical minus sign
        # Exponents.
        "1e3",
        "1E+3",
        "1e-3",
        "1e+3_4",
        "1e-0_3",
        "1e",
        "1E",
        "1e+",
        "1e-",
        "1e++2",
        "1e--2",
        "1ex",
        "1e2x",
        "1e_",
        "1e+_",
        "1e+__2",
        "1E+3e4",
        "1E+9999999999999999999",  # beyond MAX_EMAX
        "1e-9999999999999999999999999",  # beyond MAX_EMAX, negative side
        # Whitespace: leading/trailing only, Python's str.isspace() set.
        " 1.5 ",
        "\t1.5\n",
        "\u00a01.5\u00a0",
        "\u20031.5\u2003",
        "\u001c1.5\u001f",
        "\u00851.5",
        "\u200b1.5",  # zero-width space: not whitespace
        "1 .5",
        "1. 5",
        "1.5\u0000",
        # Non-finite words (case-insensitive; underscores ignored).
        "NaN",
        "nan",
        "NAN",
        "sNaN",
        "SNaN",
        "snan",
        "Inf",
        "inf",
        "INF",
        "Infinity",
        "infinity",
        "-Infinity",
        "+Infinity",
        "-inf",
        "Na_N",
        "Inf_inity",
        "  NaN  ",
        "s_NaN",
        "i_nf",
        "N_a_N",
        "-Na_N",
        "-1NaN",
        # Plain syntax refusals and non-Nd numerals.
        "",
        ".",
        "+",
        "-",
        "1.2.3",
        "\u00b2",  # superscript two (No, not Nd)
        "\u2163",  # Roman numeral four (Nl, not Nd)
    ]


def dec_token(value: Decimal) -> str:
    tup = value.as_tuple()
    digits = "".join(str(d) for d in tup.digits) if tup.digits else "0"
    return f"V:{tup.sign}|{digits}|{tup.exponent}~{value}"


def expected_for(spelling: str) -> str:
    """Record the oracle's exact accept/refuse outcome for one spelling."""
    try:
        value = Decimal(spelling)
    except InvalidOperation as exc:
        detail = str(exc)
        if "ConversionSyntax" in detail:
            return "E:ConversionSyntax"
        if "InvalidOperation" in detail:
            return "E:InvalidOperation"
        raise AssertionError(f"unexpected InvalidOperation detail {detail!r} for {spelling!r}") from exc
    if not value.is_finite():
        return "E:NonFinite"
    return dec_token(value)


def render() -> str:
    lines = []
    for spelling in spelling_cases():
        hexed = spelling.encode("utf-8").hex()
        label = ascii(spelling)
        lines.append("\t".join((hexed, label, expected_for(spelling))))
    return "\n".join(lines) + "\n"


def main() -> int:
    cases = spelling_cases()
    content = render()
    if "--check" in sys.argv:
        current = FIXTURE.read_text(encoding="utf-8") if FIXTURE.exists() else ""
        if current != content:
            print(f"FAIL: spelling fixture drift ({len(current.splitlines())} stored vs {len(cases)} generated)")
            return 1
        print(f"OK: spelling fixture matches oracle ({len(cases)} cases)")
        return 0
    FIXTURE.write_text(content, encoding="utf-8")
    counts: dict[str, int] = {}
    for spelling in cases:
        token = expected_for(spelling)
        key = token.split("|")[0].split("~")[0]
        counts[key] = counts.get(key, 0) + 1
    print(f"wrote {FIXTURE} with {len(cases)} cases")
    print("outcomes: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
