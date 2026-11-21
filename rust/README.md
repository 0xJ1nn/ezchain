# Rust migration

The accepted language policy (ADR-0050) assigns the core engine, P2P networking,
consensus/validation, RPC server, transaction execution and the shipped CLI to Rust.
Python keeps the indexer/analytics, AI/ML components and strategy plugins; TypeScript keeps
the dashboard and all product UI.

This directory is the Rust half of the repository. It is being built on the `rust/migration`
integration branch, one field at a time, while the Python platform keeps running.

## Gate for a port

A unit is not ported until:

1. its current Python behaviour is recorded as cases, including failures;
2. the Rust implementation matches every recorded case (`diff`);
3. `cargo fmt --check`, `cargo clippy`, `cargo test` and the behaviour gate pass;
4. a benchmark says whether the port is actually faster — a small function called one at a
   time is usually slower across the FFI boundary, so port a loop or a whole boundary.

The Python implementation stays the oracle until its cases pass against Rust. Money
arithmetic stays in Python until a Rust decimal can reproduce the per-operation precision of
`money.py` and a parity harness proves it.

## Workspace layout

| Crate | Owns |
|---|---|
| `ezchain-core` | Pure deterministic core kernels |
| `ezchain-rpc` | RPC/edge kernels, beginning with scoped-key normalisation |

New crates follow the same rule: a crate owns one field, has its own tests, and never contains
I/O, keys or ledger writes without an accepted ADR.

## Frozen crates

These crates are built and tested but are not deployed and receive no new work:

- `ezchain-p2p`
- `ezchain-p2p-transport`
- `ezchain-consensus`
- `ezchain-node`
- `ezchain-rpc-server`
- every module of `ezchain-rpc` other than `scopes` and `method`, the two the bridge calls.

They are frozen because no second machine has to agree on one tape. Peer transport,
consensus, the node and the node-facing RPC transport only earn a deployment when there is a
second machine to agree with; there is none today. The wire contract is already published as
`tests/contract/snapshots/openapi.json`, so that snapshot is authoritative and the RPC
transport has no consumer to satisfy.

The exact unfreeze condition: a second machine exists that must agree on one tape, and the
P2P/consensus design ADR has been accepted. Without both, these crates stay out of the shipped
product. `ezchain-seal` and `ezchain-replay` are not frozen; they are proven kernels that are
not yet called from the Python write path.
