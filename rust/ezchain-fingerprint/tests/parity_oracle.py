#!/usr/bin/env python3
"""Oracle harness for the SHA-256 transaction fingerprint.

Generates canonical payloads with the real code in ``packages/core-py`` and
records the lowercase hex SHA-256 digest each one produces. ``tests/parity.rs``
feeds the recorded bytes to Rust and requires a byte-for-byte digest match.

Two independent ties to ``ezchain_core.signing`` keep the oracle honest:

* ``payload_*`` cases use the exact bytes of ``encode_transaction(payload)``
  and assert the digest equals ``PreparedTransaction.payload_digest``.
* ``approval_*`` cases use the canonical dict built inside
  ``ApprovedIntent.digest`` and assert the digest equals ``intent.digest``.

``kat_*`` cases are raw-byte known-answer vectors from the standard SHA-256
definition, recorded through ``hashlib`` so they cover empty and short input in
the same fixture format.

Usage (from the checkout root, with the project venv active)::

    .venv/bin/python rust/ezchain-fingerprint/tests/parity_oracle.py            # write fixture
    .venv/bin/python rust/ezchain-fingerprint/tests/parity_oracle.py --check    # verify no drift

Fixture line format (tab separated, all ASCII)::

    <name>\t<hex-of-raw-bytes>\t<lowercase-hex-digest>
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
    PreparedTransaction,
    TransactionPayload,
    _canonical_decimal,
    encode_transaction,
)


def digest(raw: bytes) -> str:
    """The exact call signing.py makes: hashlib.sha256(raw).hexdigest()."""
    return hashlib.sha256(raw).hexdigest()


# --- payload cases: canonical bytes from encode_transaction ----------------

PAYLOAD_VALUES = [
    # (cluster, program_id, mint, side, amount, min_out, recipient, idempotency_key)
    ("devnet", "Prog111", "So1111111111", "buy", "1.50", "0.250", "recv-1", "idem-1"),
    ("mainnet-beta", "prog-2", "mint-2", "sell", "12.500", "3.14159", "recv-2", "idem-2"),
    ("devnet", "prog/3", 'mint"3', "buy", "1E+3", "0E+5", "recv\\3", "idem-3"),
    ("devnet", "prög-4", "mint-é", "sell", "00025.000", "-0.00", "反-cn", "idem-4"),
    ("cluster-5", "prog-5", "mint-😀", "buy", "0.0000001", "2.500E1", "tab\tkey", "id\n5"),
    ("c6", "p6", "m", "sell", "1_000.50", "0.000", "\u0000ctrl", "i6"),
]


def payload_cases() -> list[tuple[str, bytes, str]]:
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
        raw = encode_transaction(payload)
        expected = digest(raw)
        assert PreparedTransaction(raw=raw, payload=payload).payload_digest == expected
        cases.append((f"payload_{n}", raw, expected))
    return cases


# --- approval cases: canonical dict from ApprovedIntent.digest -------------

APPROVAL_VALUES = [
    # (chain, cluster, mint, side, amount, min_out, recipient, program, idem, actor)
    ("solana", "devnet", "mint-a", "buy", "1.50", "0.250", "recv-a", "prog-a", "id-a", None),
    ("solana", "mainnet", "mint-b", "sell", "12.500", "3.14159", "recv-b", "prog-b", "id-b", "actor-💼"),
    ("chain\n3", "clus\t3", 'mint"3', "buy", "1E+3", "0E+5", "recv\\3", "prog/3", "id-3", ""),
]


def approval_cases() -> list[tuple[str, bytes, str]]:
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
        raw = json.dumps(
            canonical, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        expected = digest(raw)
        assert intent.digest == expected, "oracle drifted from ApprovedIntent.digest"
        cases.append((f"approval_{n}", raw, expected))
    return cases


# --- raw-byte known-answer vectors ----------------------------------------


def kat_cases() -> list[tuple[str, bytes, str]]:
    raw_vectors = [
        ("kat_empty", b""),
        ("kat_abc", b"abc"),
        (
            "kat_short_json",
            json.dumps({"a": 1}, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        ),
        ("kat_binary", bytes(range(32))),
        ("kat_multiblock", b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
    ]
    return [(name, raw, digest(raw)) for name, raw in raw_vectors]


def all_cases() -> list[tuple[str, bytes, str]]:
    return payload_cases() + approval_cases() + kat_cases()


def render(cases: list[tuple[str, bytes, str]]) -> str:
    return "".join(
        f"{name}\t{raw.hex()}\t{expected}\n" for name, raw, expected in cases
    )


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
