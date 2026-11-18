#!/usr/bin/env python3
"""Oracle harness for the `ezchain-replay` read-only balance projection.

Builds a deterministic corpus of *constructed* ledger entries, runs every case
through the real functions in ``packages/core-py``'s ``ledger.py`` under the
project venv, and records the answer as ``tests/parity_fixture.txt``. The Rust
integration test ``tests/parity.rs`` asserts the port produces the identical
token. The Python implementation stays the accountant and the oracle; this
script writes recorded answers, it never reads Rust output.

Usage (from the checkout root, with the project venv)::

    .venv/bin/python rust/ezchain-replay/tests/parity_oracle.py            # write fixture
    .venv/bin/python rust/ezchain-replay/tests/parity_oracle.py --check    # verify no drift

Held-out corpora (a different seed and/or output path) use the same env knobs
as the other parity harnesses::

    EZ_REPLAY_SEED=7 EZ_REPLAY_FIXTURE=/tmp/held_out.txt .venv/bin/python ...
    EZ_REPLAY_FIXTURE=/tmp/held_out.txt cargo test -p ezchain-replay

Fixture grammar (tab-separated; last field is the recorded answer)::

    name    <kind> [<args>]        A:<account string>
    entry   <entry>                OK | E:<Python exception class>
    replay  <tape>                 R:<account>#<canon_dec>#<signed_minor>;... | E:...
    balance <tape>                 OK | E:LedgerError:ledger group does not balance: N minor units
    pos     <tape>                 P:<canon_dec>,<canon_dec> | E:<class>:<message>
    pnl     <tape>                 V:<canon_dec>

where an ``<entry>`` is ``account~asset~amount_text~usd_text~kind`` and a
``<tape>`` is ``<entry>;<entry>;...`` in journal order. ``canon_dec`` is
``<sign>|<coefficient digits>|<exponent>`` (Python ``Decimal.as_tuple``), the
same exact triple the ``ezchain-decimal`` oracle uses, so representation as
well as numeric value is compared.
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
SEED = int(os.environ.get("EZ_REPLAY_SEED", "20261118"))
FIXTURE = Path(os.environ.get("EZ_REPLAY_FIXTURE", str(HERE / "parity_fixture.txt")))
sys.path.insert(0, str(CORE_PY_SRC))

from ezchain_core.ledger import (  # noqa: E402
    FUNDING_ACCOUNT,
    FUNDING_PREFIX,
    LedgerEntry,
    assert_balanced,
    buy_entries,
    cash_account,
    fee_account,
    funding_entries,
    genesis_entries,
    position_account,
    realized_account,
    realized_pnl,
    replay_balances,
    replay_position,
    sell_entries,
)

LINES: list[str] = []


def canon(value: Decimal) -> str:
    """Exact Decimal triple, matching `ezchain_decimal::Decimal::canonical`."""
    tup = value.as_tuple()
    digits = "".join(str(d) for d in tup.digits) if tup.digits else "0"
    return f"{tup.sign}|{digits}|{tup.exponent}"


def enc_entry(entry: LedgerEntry) -> str:
    """Encode one entry from the exact lexemes that constructed it."""
    return f"{entry.account}~{entry.asset}~{entry.amount}~{entry.usd_value}~{entry.kind}"


def enc_tape(entries: list[LedgerEntry]) -> str:
    return ";".join(enc_entry(e) for e in entries)


def add(op: str, args: list[str], expected: str) -> None:
    LINES.append("\t".join([op, *args, expected]))


def E(account: str, amount: str, usd: str, kind: str, asset: str = "USD") -> LedgerEntry:
    """Construct a leg with canonical decimal text, as a caller would persist."""
    return LedgerEntry(account, asset, Decimal(amount), Decimal(usd), kind)


# ---------------------------------------------------------------------------
# Account naming rules
# ---------------------------------------------------------------------------
add("name", ["cash"], f"A:{cash_account()}")
add("name", ["fee"], f"A:{fee_account()}")
add("name", ["realized"], f"A:{realized_account()}")
add("name", ["funding"], f"A:{FUNDING_ACCOUNT}")
add("name", ["prefix"], f"A:{FUNDING_PREFIX}")
add("name", ["position", "solana", "TOKEN1"], f"A:{position_account('solana', 'TOKEN1')}")
add("name", ["position", "ethereum", "0xAbCd"], f"A:{position_account('ethereum', '0xAbCd')}")


# ---------------------------------------------------------------------------
# Entry construction and typed refusals (LedgerEntry.__post_init__)
# ---------------------------------------------------------------------------
ENTRY_CASES = [
    ("cash:USD", "USD", "1.5", "0.00000001", "buy"),
    ("cash:USD", "USD", "1", "0.000000009", "buy"),  # sub-minor USD
    ("cash:USD", "USD", "1", "1.000000001", "buy"),  # sub-minor USD
    ("position:sol:TOK", "TOK", "12345678901234567890123456789012345678", "2.00000000", "buy"),
    ("position:sol:TOK", "TOK", "-2", "1.00000000", "sell"),
    ("cash:USD", "USD", "NaN", "1.00000000", "buy"),  # non-finite amount
    ("cash:USD", "USD", "abc", "1.00000000", "buy"),  # malformed literal
    ("cash:USD", "USD", "1E+22", "1.00000000", "buy"),
]
for account, asset, amount, usd, kind in ENTRY_CASES:
    try:
        LedgerEntry(account, asset, Decimal(amount), Decimal(usd), kind)
        expected = "OK"
    except Exception as exc:  # noqa: BLE001 - the class name is the oracle token
        expected = f"E:{type(exc).__name__}"
    add("entry", [f"{account}~{asset}~{amount}~{usd}~{kind}"], expected)


# ---------------------------------------------------------------------------
# Real journal groups from the Python constructors (the accountant's own legs)
# ---------------------------------------------------------------------------
genesis = list(genesis_entries(Decimal("1000")))
funding = list(funding_entries(Decimal("250.5")))
buy = list(
    buy_entries(
        chain="solana",
        token_address="TOKEN1",
        quantity=Decimal("2.5"),
        price_usd=Decimal("10"),
        fee_bps=30,
    )
)
sell = list(
    sell_entries(
        chain="solana",
        token_address="TOKEN1",
        quantity=Decimal("1"),
        price_usd=Decimal("12"),
        avg_cost_usd=Decimal("10"),
        fee_bps=30,
    )
)


def replay_token(entries: list[LedgerEntry]) -> str:
    parts = []
    for balance in replay_balances(entries):
        parts.append(f"{balance.account}#{canon(balance.amount)}#{balance.usd_minor}")
    return "R:" + ";".join(parts)


def balance_token(entries: list[LedgerEntry]) -> str:
    try:
        assert_balanced(entries)
        return "OK"
    except Exception as exc:  # noqa: BLE001 - class + message are the oracle token
        return f"E:{type(exc).__name__}:{exc}"


def pos_token(entries: list[LedgerEntry]) -> str:
    try:
        quantity, cost = replay_position(entries)
        return f"P:{canon(quantity)},{canon(cost)}"
    except Exception as exc:  # noqa: BLE001
        return f"E:{type(exc).__name__}:{exc}"


def pnl_token(entries: list[LedgerEntry]) -> str:
    try:
        return f"V:{canon(realized_pnl(entries))}"
    except Exception as exc:  # noqa: BLE001
        return f"E:{type(exc).__name__}:{exc}"


def record_tape(name: str, entries: list[LedgerEntry], *, with_pos: bool = False) -> None:
    tape = enc_tape(entries)
    add("replay", [tape], replay_token(entries))
    add("balance", [tape], balance_token(entries))
    if with_pos:
        add("pos", [tape], pos_token(entries))


# Single balanced groups, each asserted by the accountant's constructors.
record_tape("genesis", genesis)
record_tape("funding", funding)
record_tape("buy", buy)
record_tape("sell", sell, with_pos=False)

# A whole ordered tape: genesis + top-up + buy + sell.
whole = genesis + funding + buy + sell
record_tape("whole", whole)
add("pnl", [enc_tape(whole)], pnl_token(whole))

# Position-only tapes (replay_position is per position account, oldest first).
record_tape(
    "position-open",
    [e for e in buy if e.account.startswith("position:")],
    with_pos=True,
)
closed = [e for e in buy + sell if e.account.startswith("position:")]
record_tape("position-closed", closed, with_pos=True)
record_tape(
    "position-negative",
    [E(position_account("solana", "TOK"), "-1", "1.00000000", "sell", "TOK")],
    with_pos=True,
)

# Exact-sum precision: two 38-digit quantities that cancel to a residue an
# ambient 28-digit context would round away.
wide = [
    E(position_account("solana", "TOK"), "12345678901234567890123456789012345678", "0.00000000", "buy", "TOK"),
    E(position_account("solana", "TOK"), "-12345678901234567890123456789012345677", "0.00000000", "sell", "TOK"),
]
record_tape("position-wide", wide, with_pos=True)

# A long, multi-account tape: every group balanced, so the whole tape is too;
# exercises account sorting and a sum longer than the ambient digit budget.
long_tape: list[LedgerEntry] = []
for i in range(12):
    long_tape += list(
        buy_entries(
            chain=f"chain{i % 3}",
            token_address=f"TOK{i}",
            quantity=Decimal(f"{i + 1}.25"),
            price_usd=Decimal(f"{i + 2}.50"),
            fee_bps=25,
        )
    )
    long_tape += list(
        sell_entries(
            chain=f"chain{i % 3}",
            token_address=f"TOK{i}",
            quantity=Decimal("1"),
            price_usd=Decimal(f"{i + 3}.50"),
            avg_cost_usd=Decimal(f"{i + 2}.50"),
            fee_bps=25,
        )
    )
record_tape("long", long_tape)

# Seeded corpora: a different EZ_REPLAY_SEED produces a genuinely different
# tape, so a held-out run cannot be satisfied by overfitting to one fixture.
rng = random.Random(SEED)
seeded_tape: list[LedgerEntry] = []
for i in range(8):
    qty = Decimal(f"{rng.randint(1, 9)}.{rng.randint(0, 99_999_999):08d}")
    price = Decimal(f"{rng.randint(1, 99)}.{rng.randint(0, 99_999_999):08d}")
    seeded_tape += list(
        buy_entries(
            chain="seeded",
            token_address=f"R{i}",
            quantity=qty,
            price_usd=price,
            fee_bps=rng.randint(0, 100),
        )
    )
record_tape("seeded", seeded_tape)

# Conservation failures: one minor out, and a longer tape with a single bad
# leg among otherwise balanced groups. The exact signed total is part of the
# recorded message.
unbalanced_one = [
    E(cash_account(), "1", "1.00000000", "buy"),
    E(fee_account(), "1", "-0.99999999", "fee"),
]
record_tape("unbalanced-one", unbalanced_one)
unbalanced_neg = [
    E(cash_account(), "1", "-1.00000000", "sell"),
    E(fee_account(), "1", "0.99999998", "fee"),
]
record_tape("unbalanced-negative", unbalanced_neg)
corrupt = buy + sell + [E(fee_account(), "1", "0.00000001", "fee")]
record_tape("unbalanced-in-long", corrupt)

# Realized PnL: a contra account with several legs, an empty tape, a zero-sum
# tape, and a >28-digit sum that exercises the ambient unary-minus rounding.
add(
    "pnl",
    [enc_tape([E(realized_account(), "1", "-5.00000000", "pnl"), E(realized_account(), "1", "3.00000000", "pnl")])],
    pnl_token([E(realized_account(), "1", "-5.00000000", "pnl"), E(realized_account(), "1", "3.00000000", "pnl")]),
)
add("pnl", [enc_tape([])], pnl_token([]))
add(
    "pnl",
    [enc_tape([E(realized_account(), "1", "5.00000000", "pnl"), E(realized_account(), "1", "-5.00000000", "pnl")])],
    pnl_token([E(realized_account(), "1", "5.00000000", "pnl"), E(realized_account(), "1", "-5.00000000", "pnl")]),
)
wide_pnl = [E(realized_account(), "1", "-12345678901234567890.12345678", "pnl")]
add("pnl", [enc_tape(wide_pnl)], pnl_token(wide_pnl))
# More than 28 significant digits: Python's unary minus rounds at the ambient
# context, which is why this port must not use copy_negate here.
ambient_pnl = [E(realized_account(), "1", "-123456789012345678901234567890.12345678", "pnl")]
add("pnl", [enc_tape(ambient_pnl)], pnl_token(ambient_pnl))


fixture_text = "\n".join(LINES) + "\n"
if "--check" in sys.argv:
    current = FIXTURE.read_text(encoding="utf-8") if FIXTURE.exists() else ""
    if current != fixture_text:
        print(f"fixture drift: {FIXTURE} differs from regenerated oracle output", file=sys.stderr)
        sys.exit(1)
    print(f"fixture OK: {len(LINES)} cases match the oracle (seed {SEED})")
else:
    FIXTURE.write_text(fixture_text, encoding="utf-8")
    print(f"wrote {len(LINES)} cases to {FIXTURE} (seed {SEED})")
