# Rust kernels — gated, not a migration

ADR-0053 keeps Python as the backend and the live execution path for 0.1.0. The Rust crates
already landed stay as kernels off that path and are not deleted. New P2P and consensus
components remain governed by ADR-0051 and ADR-0052; choosing Rust for a new component is not
a rewrite. TypeScript keeps the dashboard and all product UI.

This directory holds the Rust half of the repository. No further kernel is adopted without the
gate below.

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
