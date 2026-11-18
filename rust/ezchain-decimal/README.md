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
- `exp: i64` — base-10 exponent. It spans CPython's whole constructible range
  (`MAX_EMAX = MAX_PREC = 999999999999999999`), so exponents outside the former
  `i32` representation are handled and proven below. Larger exponents are
  refused at parse time the way `Decimal(str)` raises `InvalidOperation`.

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
| `require_money` | `require_money(value)` | yes — representation preserved, sub-quantum refusal (`SubQuantumError`), non-finite refusal |
| `require_quantity` | `require_quantity(value)` | yes — finite exact values pass without a scale limit |
| `to_minor_units` | `to_minor_units(value)` | yes — exact 1e-8 scaling, signed integer magnitude, sub-quantum and non-finite refusal, round-trip checked |
| `from_minor_units` | `from_minor_units(minor)` | yes — inverse conversion for arbitrary-precision signed integers |
| `exact_neg` | `exact_neg(value)` | yes — sign flip on the digits with no context rounding, including the sign of zero |
| Non-finite rejection | `_as_decimal` → `MoneyError` | yes — `NaN`, `sNaN`, `±Infinity` at every money boundary, including the new conversion helpers |
| Input-type guards | `_as_decimal` / `from_minor_units` | yes — `bool` → `MoneyError`, `float` → `FloatMoneyError`, `int` accepted as `Decimal(int)`, any other type → `MoneyError`; `from_minor_units(1.0)` → plain `MoneyError`, not `FloatMoneyError` |
| Ambient-context boundaries | decimal `Emax` / `MAX_PREC` under `money.py`'s `localcontext()` | yes — quantize/floor past `Emax` → `MoneyError` (caught `InvalidOperation`), product past `Emax` → `Overflow`, derived precision past `MAX_PREC` → `ValueError`, `scaleb` past `Emax` in `to_minor_units` → `MoneyError` |
| Exponents outside `i32` | `Decimal(str)` and money ops at ±1e10 and near `MAX_EMAX` | yes — parse, `str`, quantize refusal, `exact_precision`, product overflow and end-of-range construction refusal all match |
| `SourceRef.to_dict` | `types.SourceRef.to_dict` | yes — `name`, `url`/null and the UTC `datetime.isoformat()` spelling (`YYYY-MM-DDTHH:MM:SS[.ffffff]+00:00`) |
| `CostPreview.to_dict` | `money.CostPreview.to_dict` | yes — the full field map including the echoed `network_fee_usd` / `price_impact_pct`, nullable `worst_case_out`, integer `slippage_bps` and nested `source` |
| `Quote.to_dict` | `types.Quote.to_dict` | yes — money spellings plus nullable `route` / `price_impact_pct` and nested `source` |
| to_dict → canonical JSON bytes | `json.dumps(obj.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)` | yes — every recorded field map re-emitted byte-for-byte through `ezchain-jsoncanon` |

Corpus: **1087 deterministic cases** (fixed hand-picked tricky cases plus a seeded
random corpus, up to 40 digits). A **held-out corpus of 1087 cases generated with a
different seed (77123)** also matched 1087/1087, as a check against overfitting. Tricky
cases include `0.1 + 0.2`, 38-digit `NUMERIC(38, 8)` values, negative exponents,
half-even ties (`5e-9 → 0E-8`, `15e-9 → 2E-8`), floor on negatives
(`-1.234567895 → -1.23456790`), non-finite refusal, division preferred-exponent and
rounding cases (`1.00 / 2 → 0.50`, `100000 / 3` at precision 4 `→ 3.333E+4`,
`101 / 1000` at precision 2 `→ 0.10`, `1 / 3` at 28 digits), long-tail bps fees,
both preview cost-sheet branches, whole-versus-sub-quantum minor-unit conversions
(`0.1 → 10000000`, `0.000000009 → SubQuantumError`), signed minor-unit round trips,
`exact_neg` on long coefficients and signed zeros, every Python input type at both
money boundaries, the `Emax`/`MAX_PREC` refusal boundaries and exponents up to
`MAX_EMAX`.

The `to_dict` spellings have their own corpus: **592 deterministic cases** (171
`SourceRef`, 218 `CostPreview`, 203 `Quote`, recorded from the real Python
objects) with a **held-out corpus of 592 cases from a different seed (99173)**
also matching 592/592.

This proves **per-operation decimal parity and the named `to_dict` spellings
only**. It does not establish a speed advantage, an FFI boundary, or that a
whole money path is ready to move.

## Remaining blockers — NOT proven, NOT in scope

These are explicitly out of this prototype. None may be treated as ready to port:

- **Currency conversion** — no conversion between assets, quote inversion or
  token-decimal scaling is implemented.
- **SHA-256 fingerprint / seal hashing** — no hashing, no seal, no cryptography.

The previously-listed `to_dict` / JSON canonicalisation blocker is resolved for
the records named above: their field maps and canonical JSON bytes are proven.
A general canonical wire form for *every* record, and the transaction/ledger
fingerprint path, remain the job of `ezchain-jsoncanon` / `ezchain-seal`, not
this crate.

Also out of scope: context behaviour is proven only for
the **default ambient context** `money.py` actually runs under: `clamp = 0`,
`Emin = -999999`, `Emax = 999999`, with `prec` derived per operation. A
non-default `clamp`, custom `Emin`/`Emax`, and subnormal / underflow (`Emin`-edge)
behaviour are **not** proven: `money.py` never sets those fields, so this oracle
cannot record answers for them. Exponents beyond `MAX_EMAX` are refused at parse
time; they are not representable in CPython either. The context-precision
derivation is sized so that no rounding is needed for the proven operations;
`round_to_precision` and the division guard digit mirror context rounding.

## Gates (observed results)

From the `rust/` workspace, with the scratch Rust toolchain and the project venv:

```
cargo fmt --all -- --check                          # clean
cargo clippy --workspace --all-targets -- -D warnings  # clean
cargo test --workspace                              # 450 passed, 0 failed
   └─ ezchain-decimal: 22 unit tests + 1 decimal parity test (1087/1087 cases)
                        + 2 to_dict tests (592/592 cases)
.venv/bin/python rust/ezchain-decimal/tests/parity_oracle.py --check
                                                    # OK: fixture matches oracle (1087 cases)
.venv/bin/python rust/ezchain-decimal/tests/todict_oracle.py --check
                                                    # OK: fixture matches oracle (592 cases)
EZ_DECIMAL_SEED=77123 EZ_DECIMAL_FIXTURE=/tmp/heldout.txt \
  .venv/bin/python rust/ezchain-decimal/tests/parity_oracle.py
                                                     # wrote 1087 held-out cases
EZ_DECIMAL_FIXTURE=/tmp/heldout.txt \
  cargo test -p ezchain-decimal --test parity        # parity: 1087/1087 held-out cases match
EZ_TODICT_SEED=99173 EZ_TODICT_FIXTURE=/tmp/todict-heldout.txt \
  .venv/bin/python rust/ezchain-decimal/tests/todict_oracle.py
                                                      # wrote 592 held-out cases
EZ_TODICT_FIXTURE=/tmp/todict-heldout.txt \
  cargo test -p ezchain-decimal --test todict         # parity: 592/592 held-out cases match
```

Regenerate the recorded oracle fixtures with:

```
.venv/bin/python rust/ezchain-decimal/tests/parity_oracle.py
.venv/bin/python rust/ezchain-decimal/tests/todict_oracle.py
```

Set `EZ_DECIMAL_SEED` / `EZ_DECIMAL_FIXTURE` or `EZ_TODICT_SEED` /
`EZ_TODICT_FIXTURE` to generate a held-out corpus without overwriting the
committed fixture; the Rust parity tests read the path in the matching
`*_FIXTURE` variable when it is set, otherwise they use the committed fixture.

## Safety

`#![forbid(unsafe_code)]`, zero runtime dependencies, no I/O, no keys, no ledger
writes. The only Cargo dependency is a **dev-only** path dependency on the
sibling `ezchain-jsoncanon` crate, used in the parity gate to emit the canonical
byte string; the library itself stays dependency-free. No money-path caller is
modified. Money arithmetic stays in Python (CONTRACT I1, ADR-0049/0050/0052)
until a separate ADR and full-path parity gate say otherwise.
