# ezchain-seal

Pure LEDGER SEAL construction and verification order, ported from
`packages/core-py/src/ezchain_core/ledger.py`. Part of the Rust migration on
`rust/migration` (ADR-0049/0050/0052).

## What this crate owns

The deterministic hashing order only:

- the canonical UTC timestamp spelling (`%Y-%m-%dT%H:%M:%S.%fZ`);
- the per-entry chain payload (`_chain_payload`) and its canonical JSON bytes;
- the link digest (`_chain`) via `ezchain-fingerprint`'s SHA-256;
- the chained fold (`ledger_digest`) and `seal_entries`;
- the exact `verify_seal` order: count first at the seal boundary, then the
  per-entry chain head, with typed refusal codes.

## What stays in Python

Everything that constructs or moves money: `money.py`, the journal constructors,
`assert_balanced`, `replay_balances`, all ledger writes, and the Python
implementation itself as the oracle (CONTRACT I1, ADR-0050 rule 4). Amounts cross
the boundary as canonical decimal text lexemes and timestamps as canonical UTC
spellings; this crate never parses, rounds, converts a timezone or does arithmetic.

## Parity

`tests/parity_oracle.py` records cases from the real `ezchain_core.ledger` and
writes `tests/parity_fixture.txt`. `cargo test` runs `tests/parity.rs`, which
requires identical canonical payload bytes, identical digests and identical typed
refusal codes. Regenerate (and check) with the project venv:

```sh
.venv/bin/python rust/ezchain-seal/tests/parity_oracle.py           # regenerate
.venv/bin/python rust/ezchain-seal/tests/parity_oracle.py --check   # assert no drift
```
