# ezchain-jsoncanon

Canonical JSON bytes for transaction fingerprints, dependency-free and pure.
This is the last pure step before a SHA-256 fingerprint; **hashing is not
implemented here** and needs a crypto dependency plus its own decision.

## What is proven

The Python oracle is the `json.dumps` call in
`packages/core-py/src/ezchain_core/signing.py` (lines 150, 262):

```python
json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
```

with the default `ensure_ascii=True`. `tests/parity_oracle.py` records the
exact bytes this produces for real `TransactionPayload.to_dict()` values, the
canonical dict inside `ApprovedIntent.digest`, and a deterministic corpus of
escaping, ordering, scalar and refusal cases. `tests/parity.rs` asserts
byte-for-byte equality against every recorded case.

The rules carried over verbatim:

1. **Sorted keys** — object fields are emitted in Python code-point order,
   independent of insertion order (Rust `String` byte order is the same order
   for valid UTF-8).
2. **No insignificant whitespace** — separators are exactly `,` and `:`.
3. **ASCII-only strings** — `"` → `\"`, `\` → `\\`, `\b`/`\f`/`\n`/`\r`/`\t`
   use their short escapes; every other character outside U+0020–U+007E is
   `\uXXXX` (lowercase hex); characters above the BMP are surrogate pairs;
   `/` is never escaped.
4. **Integers** — arbitrary-precision decimal text, never a machine float.
5. **Decimals** — the canonical fixed-point spelling of
   `_canonical_decimal`: `format(value, "f")`, trailing zeros and point
   stripped, `-0` becomes `0`, non-finite refused. Amounts and floors are
   emitted as JSON **strings**, exactly as `signing.py` does.
6. **Booleans and null** — `true`, `false`, `null`.
7. **Failures** — non-finite values are refused with Python's `ValueError`
   class (`allow_nan=False`); values whose Python type is not serialisable
   (`Decimal`, `bytes`, `set`, …) are refused with Python's `TypeError` class.
   Refusals propagate through nested arrays and objects.

Finite Python floats are deliberately **out of scope**: they never appear in a
transaction fingerprint (money is a canonical decimal string), so this writer
refuses them as an unsupported type rather than inventing a float-repr rule
that the payload never exercises. The oracle contains no finite-float case.

## Gate

From `rust/` with the scratch toolchain:

```bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

Regenerate or check the fixture with the project venv:

```bash
.venv/bin/python rust/ezchain-jsoncanon/tests/parity_oracle.py
.venv/bin/python rust/ezchain-jsoncanon/tests/parity_oracle.py --check
```

## Scope

No dependencies, no I/O, no keys, no ledger writes, no money-path caller
changed, no hashing. A SHA-256 fingerprint cannot move until a crypto
dependency and fingerprint are accepted in their own decision.
