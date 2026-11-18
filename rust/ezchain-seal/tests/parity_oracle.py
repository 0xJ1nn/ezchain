#!/usr/bin/env python3
"""Oracle harness for the pure ledger seal order.

Generates cases with the *real* ``ezchain_core.ledger`` code: journal constructors
build the entries, ``seal_entries``/``verify_seal`` produce the recorded seal and
typed refusal, and ``_chain_payload`` yields the exact canonical JSON bytes. The
Rust parity gate (``tests/parity.rs``) feeds the canonical lexemes recorded here to
the Rust crate and requires an identical head, identical payload bytes and an
identical typed refusal code.

The Python implementation is the oracle and stays the accountant (ADR-0049/0050
rule 4, CONTRACT I1). A Rust failure here is a parity failure, not a test bug: do
not edit the fixture to go green — fix the implementation and regenerate from the
oracle.

Usage (from the checkout root, with the project venv active)::

    .venv/bin/python rust/ezchain-seal/tests/parity_oracle.py           # write fixture
    .venv/bin/python rust/ezchain-seal/tests/parity_oracle.py --check   # verify no drift

Fixture line format (tab separated, all ASCII)::

    <name>\t<previous>\t<prev_head>\t<seal_count>\t<seal_head>
    \t<verify_count>\t<verify_head>\t<expected_code>\t<payload_hexes>\t<entries>

``payload_hexes`` and ``entries`` are ``;``-separated lists. Each entry is an
``,``-separated list of 11 fields, each ``N`` (None) or ``S<hex-of-utf8>``. Hex
characters and the ``S`` prefix never produce ``,``/``;``/``\\t``, so the format is
unambiguous even for strings containing tabs, newlines or non-ASCII text.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
CORE_PY_SRC = REPO_ROOT / "packages" / "core-py" / "src"
FIXTURE = HERE / "parity_fixture.txt"
sys.path.insert(0, str(CORE_PY_SRC))

from ezchain_core.ledger import (  # noqa: E402
    LEDGER_GENESIS_DIGEST,
    LedgerEntry,
    LedgerSeal,
    LedgerTamperedError,
    _canonical_decimal,
    _canonical_timestamp,
    _chain_payload,
    buy_entries,
    funding_entries,
    genesis_entries,
    ledger_digest,
    seal_entries,
    sell_entries,
    verify_seal,
)

MINT = "So11111111111111111111111111111111111111112"


def enc(value: str | None) -> str:
    """Encode one optional string: ``N`` for None, ``S<hex>`` for Some."""
    if value is None:
        return "N"
    return "S" + value.encode("utf-8").hex()


def enc_entries(entries: tuple[LedgerEntry, ...] | list[LedgerEntry]) -> str:
    if not entries:
        return "-"
    return ";".join(
        ",".join(
            [
                enc(entry.account),
                enc(entry.asset),
                enc(_canonical_decimal(entry.amount)),
                enc(_canonical_decimal(entry.usd_value)),
                enc(entry.kind),
                enc(entry.group_id),
                enc(_canonical_timestamp(entry.created_at)),
                enc(entry.wallet_id),
                enc(entry.bucket_id),
                enc(entry.actor_kind),
                enc(entry.actor_id),
            ]
        )
        for entry in entries
    )


def payload_hexes(entries: tuple[LedgerEntry, ...] | list[LedgerEntry]) -> str:
    """Hex of each inner `_chain_payload` object's canonical JSON bytes, in order."""
    if not entries:
        return "-"
    head = LEDGER_GENESIS_DIGEST
    result = []
    for index, entry in enumerate(entries, start=1):
        inner = _chain_payload(index, entry)
        inner_raw = json.dumps(
            inner, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        result.append(inner_raw.hex())
        # Recompute the link head from the *full* frame, as Python `_chain` does, so
        # the fixture is tied to the real digest order and not just the payload.
        frame = json.dumps(
            {"previous": head, "entry": inner},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        head = hashlib.sha256(frame).hexdigest()
    assert head == seal_entries(entries).head, "oracle drifted from seal_entries"
    return ";".join(result)


def refusal(entries: tuple[LedgerEntry, ...] | list[LedgerEntry], seal: LedgerSeal) -> str:
    """The typed code Python's verify order reports for these entries and seal."""
    try:
        verify_seal(entries, seal)
        assert seal == seal_entries(entries), "OK must mean the recorded seal is exact"
        return "OK"
    except LedgerTamperedError:
        actual = seal_entries(entries)
        # Python's LedgerSeal dataclass compares count before head.
        if actual.count != seal.count:
            return "CountMismatch"
        if actual.head != seal.head:
            return "HeadMismatch"
        raise AssertionError("oracle drift: refused a seal that matches")


def case(
    name: str,
    entries: tuple[LedgerEntry, ...] | list[LedgerEntry],
    verify: LedgerSeal,
    previous: str = LEDGER_GENESIS_DIGEST,
) -> str:
    real = seal_entries(entries)
    fields = [
        name,
        previous,
        ledger_digest(entries, previous=previous),
        str(real.count),
        real.head,
        str(verify.count),
        verify.head,
        refusal(entries, verify),
        payload_hexes(entries),
        enc_entries(entries),
    ]
    assert all("\t" not in field for field in fields), name
    return "\t".join(fields) + "\n"


def all_cases() -> list[str]:
    genesis = genesis_entries(Decimal("10000"))
    funding = funding_entries(Decimal("2500"))
    buy = buy_entries(
        chain="solana",
        token_address=MINT,
        quantity=Decimal("2.5"),
        price_usd=Decimal("100.25"),
        fee_bps=25,
    )
    sell = sell_entries(
        chain="solana",
        token_address=MINT,
        quantity=Decimal("1.25"),
        price_usd=Decimal("110.5"),
        avg_cost_usd=Decimal("100.25"),
        fee_bps=25,
    )
    stamped = tuple(
        replace(
            entry,
            group_id=f"g-{i}",
            created_at=datetime(2026, 11, 18, 10, 30, i, i * 1000, tzinfo=UTC),
            wallet_id=f"w-{i}",
            bucket_id="bucket-1",
            actor_kind="agent",
            actor_id=f"actor-{i}",
        )
        for i, entry in enumerate(genesis, start=1)
    )
    unicode_entry = replace(
        genesis[0],
        account="position:solana:mint-é😀",
        asset="tok\t反",
        group_id='grp"x',
        wallet_id="w\\path",
        actor_id="actor\nline",
        created_at=datetime(2026, 11, 18, 10, 30, 59, 999999, tzinfo=UTC),
    )
    combined = genesis + funding + buy + sell + stamped
    long_tape = tuple(
        replace(genesis[0], amount=Decimal(i), usd_value=Decimal(i), kind=f"k-{i}")
        for i in range(1, 13)
    )
    corrupted = list(combined)
    corrupted[3] = replace(corrupted[3], usd_value=Decimal("-9999"))

    cases = [
        case("seal_empty", (), seal_entries(())),
        case("seal_genesis", genesis, seal_entries(genesis)),
        case("seal_funding", funding, seal_entries(funding)),
        case("seal_buy", buy, seal_entries(buy)),
        case("seal_sell", sell, seal_entries(sell)),
        case("seal_stamped", stamped, seal_entries(stamped)),
        case("seal_unicode", (unicode_entry,), seal_entries((unicode_entry,))),
        case("seal_combined", combined, seal_entries(combined)),
        case("seal_long", long_tape, seal_entries(long_tape)),
        case(
            "digest_custom_previous",
            combined,
            seal_entries(combined),
            previous="ab" * 32,
        ),
        case(
            "verify_wrong_count_truncate",
            combined,
            LedgerSeal(count=len(combined) - 1, head=seal_entries(combined).head),
        ),
        case(
            "verify_wrong_count_extend",
            combined,
            LedgerSeal(count=len(combined) + 1, head=seal_entries(combined).head),
        ),
        case("verify_corrupt_chain", corrupted, seal_entries(combined)),
        case("verify_both_wrong", combined, LedgerSeal(count=0, head="f" * 64)),
        case("verify_wrong_head_only", combined, LedgerSeal(count=len(combined), head="f" * 64)),
        case("verify_empty_vs_one", (), seal_entries(genesis)),
    ]
    return cases


def main() -> int:
    cases = all_cases()
    content = "".join(cases)
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
    codes: dict[str, int] = {}
    for line in cases:
        fields = line.split("\t")
        groups[fields[0].split("_")[0]] = groups.get(fields[0].split("_")[0], 0) + 1
        codes[fields[7]] = codes.get(fields[7], 0) + 1
    print("groups: " + ", ".join(f"{k}={v}" for k, v in sorted(groups.items())))
    print("codes: " + ", ".join(f"{k}={v}" for k, v in sorted(codes.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
