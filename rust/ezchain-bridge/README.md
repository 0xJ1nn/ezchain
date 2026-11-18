# ezchain-bridge

The in-process PyO3 bridge between Python callers and Rust kernels (ADR-0052 integration
contract). It is a thin adapter: it converts Python values into kernel inputs, calls a
pure kernel that already passed its own parity gate, and converts the result or typed
error back. It adds no behaviour of its own.

## Exposed functions

| Python | Rust kernel | Python original |
|---|---|---|
| `stale_reason(source_class: str) -> str` | `ezchain_core::freshness::stale_reason` | `ezchain_core.freshness.stale_reason` |
| `normalize_scopes(scopes: Sequence[str] \| None) -> tuple[str, ...]` | `ezchain_rpc::scopes::normalize_scopes` | `ezchain_api.services.api_keys.normalize_scopes` |
| `decimal_str(value: str \| int) -> str` | `ezchain_decimal::Decimal::parse` + `str_repr` | `str(Decimal(value))` |
| `decimal_compare(a: str \| int, b: str \| int) -> int` | `ezchain_decimal::compare` | `(a > b) - (a < b)` on `Decimal` |
| `quantize_money(value, field="amount") -> str` | `ezchain_decimal::quantize_money` | `ezchain_core.money.quantize_money` |
| `floor_money(value, field="amount") -> str` | `ezchain_decimal::floor_money` | `ezchain_core.money.floor_money` |
| `to_minor_units(value, field="amount") -> int` | `ezchain_decimal::to_minor_units` | `ezchain_core.money.to_minor_units` |
| `from_minor_units(minor: int) -> str` | `ezchain_decimal::from_minor_units` | `ezchain_core.money.from_minor_units` |
| `to_raw(amount, decimals: int) -> int` | `ezchain_decimal::to_raw` | `ezchain_adapters.quotes.to_raw` |
| `from_raw(raw: int, decimals: int) -> str` | `ezchain_decimal::from_raw` | `ezchain_adapters.quotes.from_raw` |

### Money boundary

The money functions take **decimal strings or ints only**. A string is the caller's way
of handing the bridge an exact `Decimal` literal; the bridge parses it with the
already-proven Rust decimal kernel and calls the pure money kernel. Python floats are
never accepted for money and are refused at the boundary with the original exceptions:
`FloatMoneyError` for a money value, plain `MoneyError` for a minor-unit count. Other
wrong types, non-finite spellings, sub-quantum values and out-of-range token decimals
raise the original `MoneyError`, `SubQuantumError`, `decimal.InvalidOperation` or
`NotFound` classes, with the original `str`, `code` and `message`. The exception classes
are imported at error time from `ezchain_core.money`, `ezchain_adapters.errors` and
`decimal`, so `except MoneyError` and friends keep working unchanged. If a module cannot
be imported, the bridge fails closed with a `RuntimeError` naming the PYTHONPATH
requirement instead of substituting a look-alike exception.

Refusals were a deliberate part of the recorded corpus: 49 of the 380 money cases are
errors, including every exception class above. The bridge carries no I/O, no keys and no
ledger writes; only pure, already-ported kernels are exposed.

## Build

The extension is an `abi3-py39` module; this repository's oracle interpreter is the
app's Python 3.11 venv. With the scratch toolchain used for the Rust migration:

```bash
SCRATCH=/private/var/folders/55/5z874h2x6tq_8xjr5zb2b76w0000gn/T/opencode/ez-rust-poc
export CARGO_HOME="$SCRATCH/cargo" RUSTUP_HOME="$SCRATCH/rustup" PATH="$SCRATCH/cargo/bin:$PATH"
cargo build -p ezchain-bridge
cp rust/target/debug/libezchain_bridge.dylib rust/target/debug/ezchain_bridge.so
```

### Why the macOS linker flag is required

A PyO3 `extension-module` does not link against `libpython`: the interpreter supplies
those symbols when it loads the module. Apple's linker rejects those unresolved
symbols by default. `-undefined dynamic_lookup` defers them to load time; without it
the build fails with undefined `_Py*` symbols. The flag is build-environment only and
does not change the module's behaviour. `rust/.cargo/config.toml` sets it on macOS.

## Parity tests

```bash
PYTHONPATH=apps/api/src:packages/core-py/src:packages/adapters/src \
  .venv/bin/python -m pytest rust/ezchain-bridge/tests/ -v
```

Two recorded corpora are checked against the live Python originals, not against
hard-coded values:

- `tests/cases.jsonl` — 63 cases: 10 for `stale_reason` and 53 for `normalize_scopes`
  (7 successful grants, 46 refusals).
- `tests/money_cases.jsonl` — 380 cases across the eight money functions:
  `decimal_str` 69, `quantize_money` 65, `floor_money` 50, `to_minor_units` 48,
  `decimal_compare` 43, `to_raw` 37, `from_raw` 36, `from_minor_units` 32.
  Outcomes: 331 successes and 49 refusals (`MoneyError` 31, `InvalidOperation` 6,
  `SubQuantumError` 5, `NotFound` 4, `FloatMoneyError` 3).

Each case calls both the Python original and the built module and compares the success
value, or the exception's exact class, `str`, `code` and `message`. Set
`EZCHAIN_BRIDGE_SO` if the built module lives outside `rust/target`. Set
`EZCHAIN_BRIDGE_MONEY_CASES` to run a held-out corpus generated with a different seed;
the pinned counts are asserted only for the committed corpus.

## Oracle and scope

Python remains the behavioural oracle until the whole migrated surface passes parity.
A recorded case is never changed to make the Rust side pass. **The ledger remains the
sole accountant (CONTRACT I1): exposing these kernels does not make Rust an accountant,
does not change any production caller and does not touch the money-path execution
flow.** Money arithmetic stays in Python at the call site; these functions are available
for a caller that explicitly chooses to use the proven kernel. No speedup is claimed —
a single call across FFI costs more than a small kernel, so any performance claim needs
a loop or a whole service boundary and a recorded benchmark (ADR-0050).

The money functions are the eight listed above only. The rest of `ezchain-decimal`
(`exact_sum`, `exact_product`, `bps_fee`, `preview_cost`, `require_money`,
`require_quantity`, `exact_neg`, `quote_price`, `value_impact`, the `to_dict`
serialisers and division) and every network-dependent quote path stay in Python and are
not wired through this bridge.
