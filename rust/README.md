# Rust kernels — the measured boundary

ADR-0054 sets the boundary: Python owns the product process, the API and the live path. Rust
owns the signer boundary, the parity-proven money kernels, the independently verifiable seal
and replay, and the shipped CLI; a capability-scoped WASM plugin host is a later, measured
step. P2P and consensus stay frozen until a second machine must agree on the same tape;
unreachable and duplicate crates are not migration progress. Custody is a separate decision
(ADR-0055). TypeScript keeps all product UI.

This directory holds the Rust half of the repository. The parity-proven money kernels are
already called from production; any further kernel enters only through the gate below.

## Gate for a port

A unit is not ported until:

1. its current Python behaviour is recorded as cases, including failures;
2. the Rust implementation matches every recorded case (`diff`);
3. `cargo fmt --check`, `cargo clippy`, `cargo test` and the behaviour gate pass;
4. a recorded benchmark fails a declared target for that kernel — parity alone is not a
   reason to ship, and "compiles and matches" is not a speed result.

One kernel at a time enters behind a reversible flag. No existing money or execution code is
ported. The Python implementation stays the oracle until its cases pass against Rust. Money
arithmetic stays in Python until a Rust decimal can reproduce the per-operation precision of
`money.py` and a parity harness proves it.

## Workspace layout

| Crate | Owns |
|---|---|
| `ezchain-core` | Pure deterministic core kernels |
| `ezchain-rpc` | RPC/edge kernels, beginning with scoped-key normalisation |

New crates follow the same rule: a crate owns one field, has its own tests, and never contains
I/O, keys or ledger writes without an accepted ADR.
