# ezchain-decimal — decimal parity prototype

**Status: research prototype. Money arithmetic is NOT enabled in Rust by this crate.**

ADR-0050 rule 4 forbids porting `money.py`, ledger entries or seal hashing until a
Rust decimal can reproduce the per-operation precision of `money.py` and a parity
harness proves it (`rust_decimal`'s fixed 28 digits cannot). This crate is that
proof-of-concept: a dependency-free decimal value whose operations are recorded
against the real Python oracle. It changes no money-path caller, adds no PyO3
binding, and enables no Rust money arithmetic anywhere.

## The value

`Decimal` is `sign + coefficient + exponent`:

- `sign: bool` — the sign of a zero is preserved (`Decimal('-0')` stays negative).
- `coef: BigUint` — an arbitrary-precision unsigned integer stored as decimal
  digits, **not** a fixed 28- or 38-digit word. There is no precision cap in the
  value itself; every operation derives the exact context it needs from the
  operands, the way `money.py` sizes its `localcontext()`.
- `exp: i32` — base-10 exponent. The 38-digit `NUMERIC(38, 8)` column and values
  far beyond it are handled; exponents outside the `i32` range are out of scope.

Formatting reproduces `str(Decimal)` (including `0E-8` for a zero at exponent -8),
because `money.py` serialises values with `str()`.

## Proven operations (parity recorded against the Python oracle)

Each operation below is implemented to match `packages/core-py/src/ezchain_core/money.py`
under the local context precision that module derives. The oracle harness
(`tests/parity_oracle.py`) runs the real Python functions under the project venv
and records their results in `tests/parity_fixture.txt`; `tests/parity.rs` asserts
the Rust output matches every recorded token, including exact triples
(`sign|coefficient|exponent`) and `str(Decimal)` forms, and refusal codes.

| Operation | `money.py` / Decimal counterpart | Proven |
|---|---|---|
| Construction from string | `Decimal(str)` | yes — finite literals, sign/zero/leading-zero/exponent handling |
| Comparison | `==`, `<`, `>` on `Decimal` | yes — representation-insensitive (`0.1 == 0.10`, `-0 == 0`) |
| Addition (under derived context) | `a + b` used by `exact_sum` | yes |
| Multiplication (under derived context) | `a * b` used by `exact_product` | yes |
| `exact_precision` | `exact_precision(*values, extra=…)` | yes |
| `exact_sum` | `exact_sum(values)` | yes |
| `exact_product` | `exact_product(*values)` | yes |
| Quantize, half-even | `quantize_money` / `ROUND_HALF_EVEN` to 1e-8 | yes — ties, both signs |
| Quantize, floor | `floor_money` / `ROUND_FLOOR` to 1e-8 | yes — including negatives |
| Division (context-rounded) | `a / b` under a derived precision, half-even | yes — preferred exponent, terminating and non-terminating quotients, half-even ties |
| `bps_fee` | `bps_fee(notional, fee_bps)` | yes — exact fee at 1e-8, negative-fee refusal |
| `preview_cost` (numeric sheet) | `preview_cost(...)` fee legs and slippage window | yes — paper-mark flat fee, `max(bps leg, quantized venue leg)`, quantized `worst_case_out`; wire form stays blocked |
| Non-finite rejection | `_as_decimal` → `MoneyError` | yes — `NaN`, `sNaN`, `±Infinity` at every money boundary |

Corpus: **659 deterministic cases** (fixed hand-picked tricky cases plus a seeded
random corpus, up to 40 digits). A **held-out corpus of 659 cases generated with a
different seed (77123)** also matched 659/659, as a check against overfitting. Tricky
cases include `0.1 + 0.2`, 38-digit `NUMERIC(38, 8)` values, negative exponents,
half-even ties (`5e-9 → 0E-8`, `15e-9 → 2E-8`), floor on negatives
(`-1.234567895 → -1.23456790`), non-finite refusal, division preferred-exponent and
rounding cases (`1.00 / 2 → 0.50`, `100000 / 3` at precision 4 `→ 3.333E+4`,
`101 / 1000` at precision 2 `→ 0.10`, `1 / 3` at 28 digits), long-tail bps fees and
both preview cost-sheet branches.

This proves **per-operation decimal parity only**. It does not establish a speed
advantage, an FFI boundary, or that a whole money path is ready to move.

## Remaining blockers — NOT proven, NOT in scope

These are explicitly out of this prototype. None may be treated as ready to port:

- **Currency conversion** — no conversion between assets, quote inversion or
  token-decimal scaling is implemented.
- **JSON canonicalisation** — no canonical wire form; `to_dict`/JSON behaviour is
  not reproduced, including `CostPreview.to_dict` and `SourceRef` serialisation.
- **SHA-256 fingerprint / seal hashing** — no hashing, no seal, no cryptography.

Also not ported: `require_money`, `require_quantity`, `to_minor_units`,
`from_minor_units`, `exact_neg`, the echoed `network_fee_usd` /
`price_impact_pct` fields of a cost sheet, ambient-context overflow /
`Emin`/`Emax`/`clamp`, context sizes that would raise Python's
`InvalidOperation`, Python's `bool`/`float`/`int` input guards (the prototype is
string-only), and exponents outside `i32`. The context-precision derivation is
sized so that no rounding is needed for the proven operations in this scope;
`round_to_precision` and the division guard digit mirror context rounding, but
behaviour at the extreme InvalidOperation boundary is not claimed.

## Gates (observed results)

From the `rust/` workspace, with the scratch Rust toolchain and the project venv:

```
cargo fmt --all -- --check                          # clean
cargo clippy --workspace --all-targets -- -D warnings  # clean
cargo test --workspace                              # 331 passed, 0 failed
   └─ ezchain-decimal: 14 unit tests + 1 parity test (659/659 cases)
.venv/bin/python rust/ezchain-decimal/tests/parity_oracle.py --check
                                                    # OK: fixture matches oracle (659 cases)
EZ_DECIMAL_SEED=77123 EZ_DECIMAL_FIXTURE=/tmp/heldout.txt \
  .venv/bin/python rust/ezchain-decimal/tests/parity_oracle.py
                                                     # wrote 659 held-out cases
EZ_DECIMAL_FIXTURE=/tmp/heldout.txt \
  cargo test -p ezchain-decimal --test parity        # parity: 659/659 held-out cases match
```

Regenerate the recorded oracle fixture with:

```
.venv/bin/python rust/ezchain-decimal/tests/parity_oracle.py
```

Set `EZ_DECIMAL_SEED` / `EZ_DECIMAL_FIXTURE` to generate a held-out corpus without
overwriting the committed fixture; the Rust parity test reads the path in
`EZ_DECIMAL_FIXTURE` when it is set, otherwise it uses the committed fixture.

## Safety

`#![forbid(unsafe_code)]`, zero dependencies, no I/O, no keys, no ledger writes.
No money-path caller is modified. Money arithmetic stays in Python (CONTRACT I1,
ADR-0049/0050/0052) until a separate ADR and full-path parity gate say otherwise.
