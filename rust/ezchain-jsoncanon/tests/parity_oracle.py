#!/usr/bin/env python3
"""Oracle harness for the canonical transaction-fingerprint JSON writer.

Generates a deterministic corpus, runs every case through the real Python
canonicalisation in ``packages/core-py`` and records the exact bytes that
``json.dumps(..., sort_keys=True, separators=(",", ":"), allow_nan=False)``
produces (with the default ``ensure_ascii=True``). ``tests/parity.rs`` asserts
the Rust writer matches every recorded byte.

Usage (from the checkout root, with the project venv active)::

    .venv/bin/python rust/ezchain-jsoncanon/tests/parity_oracle.py            # write fixture
    .venv/bin/python rust/ezchain-jsoncanon/tests/parity_oracle.py --check    # verify no drift

Fixture line format (tab separated, all ASCII)::

    <name>\t<spec>\t<expected>

``spec`` is a small prefix grammar that the Rust test decodes into its
``JsonValue`` tree. Strings are hex-encoded UTF-8; every token is ASCII, so a
line is unambiguous even when the JSON output contains control characters::

    s:<hex>            JSON string
    i:<digits>         arbitrary-precision integer
    d:<lexeme>         Decimal, canonicalised as signing.py does, then a JSON string
    b:0 | b:1          bool
    z                  null
    nf:nan|inf|-inf    Python float that is not finite (refused, allow_nan=False)
    u:<type name>      Python value whose type is not JSON serialisable (refused)
    a[<v>,<v>,...]     array
    o{<hexkey>:<v>,...} object; insertion order is deliberately irrelevant

``expected`` is the lowercase hex of the UTF-8 JSON bytes, or ``E:<ExceptionClass>``
when the Python original refused the value.

The Python implementation stays the oracle until the Rust gate passes; this
script writes its recorded answers, it does not read Rust output.
"""

from __future__ import annotations

import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
CORE_PY_SRC = REPO_ROOT / "packages" / "core-py" / "src"
FIXTURE = HERE / "parity_fixture.txt"
sys.path.insert(0, str(CORE_PY_SRC))

from ezchain_core.signing import (  # noqa: E402
    TRANSACTION_SCHEMA,
    ApprovedIntent,
    TransactionPayload,
    _canonical_decimal,
    encode_transaction,
)


def dump(value: object) -> str:
    """The exact call signing.py makes, including the default ensure_ascii=True."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


# --- spec encoders -------------------------------------------------------


def h(text: str) -> str:
    return text.encode("utf-8").hex()


def s(text: str) -> str:
    return "s:" + h(text)


def i(value: int) -> str:
    return "i:" + str(value)


def d(lexeme: str) -> str:
    return "d:" + lexeme


def b(value: bool) -> str:
    return "b:1" if value else "b:0"


def z() -> str:
    return "z"


def nf(kind: str) -> str:
    return "nf:" + kind


def u(name: str) -> str:
    return "u:" + name


def arr(*items: str) -> str:
    return "a[" + ",".join(items) + "]"


def obj(*pairs: tuple[str, str]) -> str:
    # Insertion order is intentionally not sorted: the encoder must sort keys.
    return "o{" + ",".join(f"{h(key)}:{value}" for key, value in pairs) + "}"


def expected(value: object) -> str:
    try:
        return dump(value).encode("utf-8").hex()
    except Exception as exc:  # noqa: BLE001 - the class name is the recorded answer
        return f"E:{type(exc).__name__}"


# --- cases ----------------------------------------------------------------

PAYLOAD_VALUES = [
    # (cluster, program_id, mint, side, amount, min_out, recipient, idempotency_key)
    ("devnet", "Prog111", "So1111111111", "buy", "1.50", "0.250", "recv-1", "idem-1"),
    ("mainnet-beta", "prog-2", "mint-2", "sell", "12.500", "3.14159", "recv-2", "idem-2"),
    ("devnet", "prog/3", "mint\"3", "buy", "1E+3", "0E+5", "recv\\3", "idem-3"),
    ("devnet", "prög-4", "mint-é", "sell", "00025.000", "-0.00", "反-cn", "idem-4"),
    ("cluster-5", "prog-5", "mint-😀", "buy", "0.0000001", "2.500E1", "tab\tkey", "id\n5"),
    ("c6", "p6", "m", "sell", "1_000.50", "0.000", "\u0000ctrl", "i6"),
]


def payload_cases() -> list[tuple[str, str, str]]:
    """Cases from the real ``encode_transaction`` byte producer."""
    cases = []
    for n, (cluster, program, mint, side, amount, min_out, recipient, idem) in enumerate(
        PAYLOAD_VALUES
    ):
        payload = TransactionPayload(
            cluster=cluster,
            program_id=program,
            mint=mint,
            side=side,
            amount=Decimal(amount),
            min_out=Decimal(min_out),
            recipient=recipient,
            idempotency_key=idem,
        )
        raw = encode_transaction(payload).decode("utf-8")
        # Rebuild the same dict in deliberately shuffled insertion order. The
        # writer must sort it back to the order encode_transaction uses.
        spec = obj(
            ("idempotency_key", s(idem)),
            ("min_out", d(min_out)),
            ("schema", s(TRANSACTION_SCHEMA)),
            ("recipient", s(recipient)),
            ("amount", d(amount)),
            ("mint", s(mint)),
            ("program_id", s(program)),
            ("side", s(side)),
            ("cluster", s(cluster)),
        )
        cases.append((f"payload_{n}", spec, raw.encode("utf-8").hex()))
    return cases


APPROVAL_VALUES = [
    # (chain, cluster, mint, side, amount, min_out, recipient, program, idem, actor)
    ("solana", "devnet", "mint-a", "buy", "1.50", "0.250", "recv-a", "prog-a", "id-a", None),
    ("solana", "mainnet", "mint-b", "sell", "12.500", "3.14159", "recv-b", "prog-b", "id-b", "actor-💼"),
    ("chain\n3", "clus\t3", "mint\"3", "buy", "1E+3", "0E+5", "recv\\3", "prog/3", "id-3", ""),
]


def approval_cases() -> list[tuple[str, str, str]]:
    """Cases from the dict inside ``ApprovedIntent.digest`` (signing.py:137-151)."""
    cases = []
    for n, (
        chain,
        cluster,
        mint,
        side,
        amount,
        min_out,
        recipient,
        program,
        idem,
        actor,
    ) in enumerate(APPROVAL_VALUES):
        intent = ApprovedIntent(
            chain=chain,
            cluster=cluster,
            mint=mint,
            side=side,
            amount=Decimal(amount),
            min_out=Decimal(min_out),
            recipient=recipient,
            program_id=program,
            idempotency_key=idem,
            actor=actor,
        )
        canonical = {
            "schema": TRANSACTION_SCHEMA,
            "chain": chain,
            "cluster": cluster,
            "mint": mint,
            "side": side,
            "amount": _canonical_decimal(intent.amount),
            "min_out": _canonical_decimal(intent.min_out),
            "recipient": recipient,
            "program_id": program,
            "idempotency_key": idem,
            "actor": actor,
        }
        raw = dump(canonical)
        # Tie the recorded bytes to the real digest: if this fails the oracle
        # has drifted from signing.py and the fixture must not be trusted.
        assert hashlib.sha256(raw.encode("utf-8")).hexdigest() == intent.digest
        spec = obj(
            ("actor", z() if actor is None else s(actor)),
            ("idempotency_key", s(idem)),
            ("program_id", s(program)),
            ("recipient", s(recipient)),
            ("min_out", d(min_out)),
            ("amount", d(amount)),
            ("side", s(side)),
            ("mint", s(mint)),
            ("cluster", s(cluster)),
            ("chain", s(chain)),
            ("schema", s(TRANSACTION_SCHEMA)),
        )
        cases.append((f"approval_{n}", spec, raw.encode("utf-8").hex()))
    return cases


# Every control character, the ASCII specials, and Unicode boundary codepoints.
ESCAPE_CHARS = (
    [chr(cp) for cp in range(0x00, 0x20)]
    + ['"', "\\", "/", "\u007f", "\u0080", "\u00ff", "\u0100"]
    + ["\u07ff", "\u0800", "\u2028", "\u2029", "\uffff"]
    + ["\U00010000", "\U0001f600", "\U0010ffff"]
)


def escape_cases() -> list[tuple[str, str, str]]:
    cases = []
    for cp, ch in enumerate(ESCAPE_CHARS):
        cases.append((f"escape_{cp:02d}", obj(("k", s(ch))), expected({"k": ch})))
    all_controls = "".join(chr(cp) for cp in range(0x00, 0x20))
    cases.append(("escape_all_controls", obj(("k", s(all_controls))), expected({"k": all_controls})))
    mixed = 'a"b\\c\nd\te\u0000f😀'
    cases.append(("escape_mixed", obj(("k", s(mixed))), expected({"k": mixed})))
    return cases


def ordering_cases() -> list[tuple[str, str, str]]:
    specs_and_values = [
        (obj(("b", i(1)), ("a", i(2))), {"b": 1, "a": 2}),
        (obj(("A", i(1)), ("a", i(2)), ("B", i(3)), ("b", i(4))), {"A": 1, "a": 2, "B": 3, "b": 4}),
        (obj(("\u00e9", z()), ("A", z()), ("😀", z()), ("\u0000", z())), {"\u00e9": None, "A": None, "😀": None, "\u0000": None}),
        (obj(("10", z()), ("2", z()), ("1", z())), {"10": None, "2": None, "1": None}),
        (obj(("z", i(1)), ("a", arr(b(1), z(), s("x"))), ("m", obj(("q", b(0)), ("p", d("1.50"))))),
         {"z": 1, "a": [True, None, "x"], "m": {"q": False, "p": "1.5"}}),
    ]
    return [(f"order_{n}", spec, expected(value)) for n, (spec, value) in enumerate(specs_and_values)]


def scalar_cases() -> list[tuple[str, str, str]]:
    raw = [
        ("bool_true", b(True), True),
        ("bool_false", b(False), False),
        ("null", z(), None),
        ("int_zero", i(0), 0),
        ("int_negative", i(-1), -1),
        ("int_big", i(123456789012345678901234567890), 123456789012345678901234567890),
        ("int_i64_min", i(-(2**63)), -(2**63)),
        ("int_i64_max", i(2**63 - 1), 2**63 - 1),
        ("empty_string", s(""), ""),
        ("empty_array", arr(), []),
        ("empty_object", obj(), {}),
        ("nested_array", arr(i(1), b(0), z(), s("s")), [1, False, None, "s"]),
    ]
    cases = [(name, spec, expected(value)) for name, spec, value in raw]
    for n, lexeme in enumerate(
        [
            "1.50", "1.5", "1E+3", "-0.00", "-0", "0.0000001", "1_000.50",
            " 25 ", "0E+5", "123.4500", "0.000", "-2.500E1",
            "1.", ".5", "1.e2", "+7", "-0.000", "9.999E-3", "1e1_0", "1_0.",
        ]
    ):
        canonical = _canonical_decimal(Decimal(lexeme))
        cases.append((f"decimal_{n:02d}", d(lexeme), expected(canonical)))
    return cases


def rejection_cases() -> list[tuple[str, str, str]]:
    raw = [
        ("reject_nan", nf("nan"), float("nan")),
        ("reject_inf", nf("inf"), float("inf")),
        ("reject_neg_inf", nf("-inf"), float("-inf")),
        ("reject_nested_nan", obj(("a", arr(z(), nf("nan")))), {"a": [None, float("nan")]}),
        ("reject_decimal", u("Decimal"), Decimal("1.5")),
        ("reject_bytes", u("bytes"), b"x"),
        ("reject_set", u("set"), {1, 2}),
        ("reject_nested_bytes", obj(("ok", i(1)), ("bad", u("bytes"))), {"ok": 1, "bad": b"x"}),
        ("reject_dec_inf", d("Infinity"), None),
        ("reject_dec_nan", d("sNaN"), None),
    ]
    cases = []
    for name, spec, value in raw:
        if name.startswith("reject_dec_"):
            lexeme = spec[2:]
            try:
                _canonical_decimal(Decimal(lexeme))
                exp = expected("unreachable")
            except Exception as exc:  # noqa: BLE001
                exp = f"E:{type(exc).__name__}"
        else:
            exp = expected(value)
        cases.append((name, spec, exp))
    return cases


def all_cases() -> list[tuple[str, str, str]]:
    return (
        payload_cases()
        + approval_cases()
        + escape_cases()
        + ordering_cases()
        + scalar_cases()
        + rejection_cases()
    )


def render(cases: list[tuple[str, str, str]]) -> str:
    return "".join("\t".join(case) + "\n" for case in cases)


def main() -> int:
    cases = all_cases()
    content = render(cases)
    if "--check" in sys.argv:
        current = FIXTURE.read_text(encoding="utf-8") if FIXTURE.exists() else ""
        if current != content:
            print(f"FAIL: fixture drift ({len(current.splitlines())} stored vs {len(cases)} generated)")
            return 1
        print(f"OK: fixture matches oracle ({len(cases)} cases)")
        return 0
    FIXTURE.write_text(content, encoding="utf-8")
    print(f"wrote {FIXTURE} with {len(cases)} cases")
    groups: dict[str, int] = {}
    for name, _, _ in cases:
        prefix = name.split("_")[0]
        groups[prefix] = groups.get(prefix, 0) + 1
    print("groups: " + ", ".join(f"{k}={v}" for k, v in sorted(groups.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
