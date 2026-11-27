# ezchain-p2p-transport

A **minimal P2P transport kernel** for ezCHAIN: a real TCP listener and dialer built on
`tokio` that moves already-validated envelopes between operator-labelled peers and feeds
them into the pure `ezchain_p2p::kernel::GossipKernel`.

This crate is the network edge designed (but deliberately not built) in ADR-0051 and assigned
to Rust by ADR-0052. It carries no protocol policy of its own: the validator set, set version,
fanout, envelope limits, queue and dedup capacities and frame bound are all **caller
parameters**, because ADR-0051 is still **Proposed** and decides none of those numbers
(CONTRACT I7).

## What it is

- Binds an **ephemeral `127.0.0.1` TCP port**, accepts inbound connections and dials outbound
  ones (`Node::bind`, `Node::accept_peer`, `Node::connect_peer`).
- Reads **length-prefixed frames** from each connection. The frame body is the envelope's
  *existing canonical encoding* (`ezchain_p2p::envelope::canonical_bytes`) plus its 64-bit
  digest; the framing layer is a 4-byte little-endian length prefix.
- Feeds every decoded envelope into `GossipKernel::handle`. Validation, membership, dedup,
  queueing, fanout selection and the replayable trace remain entirely in the pure kernel —
  this crate never re-implements them.
- **Writes accepted fanout deliveries** to the connections whose operator-supplied labels
  appear in the kernel's `Delivered.fanout`. Refusals are traced by the kernel and are never
  forwarded.
- Has typed connection/protocol errors, a bounded frame size and bounded queues, rejects
  malformed input without panicking, and shuts down cleanly (`Node::shutdown`).

Peer ids are **opaque labels supplied by the operator** for each connection. There is no
handshake, so the operator binds a label when accepting or dialling; the label is never
derived from, or trusted as, an authenticated identity.

## What it is NOT

This is a **node transport kernel**, not:

- **Peer discovery.** There is no bootstrap, gossip of addresses, DHT or `connect` on a
  timer. The operator names the peers and their addresses out of band.
- **An authenticated identity or handshake.** No node authentication, no keys, no signatures
  and no certificates live here. The envelope digest is the kernel's non-cryptographic
  content id / in-process tamper evidence, not a signature or a security boundary.
- **Money, ledger or live execution.** No wallet addresses, no money arithmetic, no ledger
  writes and no path to the executor. The ledger stays the only accountant (CONTRACT I1),
  keys stay in `services/executor` (I4), and live money / vault keys stay **owner-blocked**
  (ADR-0026, ADR-0035, ADR-0050 clause 3, ADR-0051 §"Live money and keys").
- **Consensus.** Fork choice, quorum certificates, leader election and liveness are designed
  in ADR-0051 but are out of scope here; ADR-0051's quorum/set-size decisions remain open
  parameters, never literals in this crate.

**Node authentication and real signatures remain a separate decision.** Wiring a signed
envelope or an authenticated peer id into this transport requires an accepted ADR; it is not
implied by this implementation.

## Dependency policy

Exactly one external dependency: `tokio`, pinned with `=1.54.1` (MSRV 1.85, MIT). Tokio is
the standard, widely audited async runtime and is the natural fit for the Rust-owned
networking layer (ADR-0052). Its own transitive dependencies (mio, bytes, socket2, …) ship as
part of tokio; this crate adds no other direct external crate. `ezchain-p2p` is an internal
workspace path dependency and remains dependency-free.

## Wire contract

```
frame := u32_le(body_len) || body
body  := canonical_bytes(envelope) || u64_le(envelope.digest)
```

`canonical_bytes` is the kernel's existing length-prefixed, fixed-field-order encoding of
`schema`, `kind`, `sender`, `set_version`, `seq`, `payload` and `timestamp` — the digest is
appended on the wire so the receiver can hand a complete `Envelope` back to `validate`.
`body_len` must be in `1..=max_frame_bytes`; every inner length is checked against the
remaining bytes before allocation. A parser failure closes only that connection; it never
panics and never reaches the kernel as an empty command (fail closed, CONTRACT I3).

## Verification

```
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

The integration test starts two nodes on ephemeral localhost ports, connects one to the
other, delivers one valid envelope, asserts the receiving kernel accepted it with the
expected trace and replay digest, and shuts both nodes down cleanly. A second test proves
accepted envelopes are forwarded to a connected fanout peer; codec unit tests cover malformed
input. No external network is used.
