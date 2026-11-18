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

Scope refusals are raised as the **original** `ApiKeyError` class, imported at error
time from `ezchain_api.services.api_keys`, with the original `code`, `message` and
`str(exc)`. Callers using `except ApiKeyError` therefore see no semantic change. If
that module cannot be imported, the bridge fails closed with a `RuntimeError` naming
the PYTHONPATH requirement instead of substituting a look-alike exception.

Money arithmetic, keys, I/O and ledger writes never cross this boundary. Only pure,
already-ported kernels are exposed.

## Build

The extension is an `abi3-py39` module; this repository's oracle interpreter is the
app's Python 3.11 venv. With the scratch toolchain used for the Rust migration:

```bash
SCRATCH=/private/var/folders/55/5z874h2x6tq_8xjr5zb2b76w0000gn/T/opencode/ez-rust-poc
export CARGO_HOME="$SCRATCH/cargo" RUSTUP_HOME="$SCRATCH/rustup" PATH="$SCRATCH/cargo/bin:$PATH"
export RUSTFLAGS="-C link-arg=-undefined -C link-arg=dynamic_lookup"
cargo build -p ezchain-bridge
cp rust/target/debug/libezchain_bridge.dylib rust/target/debug/ezchain_bridge.so
```

### Why the macOS linker flag is required

A PyO3 `extension-module` does not link against `libpython`: the interpreter supplies
those symbols when it loads the module. Apple's linker rejects those unresolved
symbols by default. `-undefined dynamic_lookup` defers them to load time; without it
the build fails with undefined `_Py*` symbols. The flag is build-environment only and
does not change the module's behaviour.

## Parity test

```bash
PYTHONPATH=apps/api/src:packages/core-py/src \
  /Users/0xj1n/Projects/ezchain/.venv/bin/python \
  -m pytest rust/ezchain-bridge/tests/test_bridge_parity.py -v
```

`tests/cases.jsonl` records 63 cases: 10 for `stale_reason` and 53 for
`normalize_scopes` (7 successful grants, 46 refusals including empty, duplicate,
unknown, case-changed and Unicode scopes). The test calls both the Python original and
the built module for every case and compares the success value, or the exception's
exact class, `str`, `code` and `message`. Set `EZCHAIN_BRIDGE_SO` if the built module
lives outside `rust/target`.

## Oracle and scope

Python remains the behavioural oracle until the whole migrated surface passes parity.
A recorded case is never changed to make the Rust side pass. This crate exposes two
small kernels to prove the boundary; a performance win requires a loop or a whole
service boundary, because a single small call across FFI costs more than the kernel
itself (see the 2026-11-18 parity proof, ADR-0050).
