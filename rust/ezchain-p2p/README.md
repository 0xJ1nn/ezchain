# ezchain-p2p — pure peer-protocol kernels

ADR-0051 (`docs/DECISIONS.md`) designs a permissioned replicated log with pure validation
kernels. This crate implements those kernels. It is **a protocol kernel, not a transport**.

## What this is

A deterministic, dependency-free kernel that decides, for each inbound envelope, whether it is
admitted, whether it is a duplicate, which peers it should be forwarded to, and what the replayable
decision trace says. It composes:

| Module | Responsibility | Oracle |
|---|---|---|
| `envelope` | Validate an envelope; compute its deterministic content id; typed rejections | ADR-0051 §"Message contract" |
| `peer` | Bounded, ordered operator-provisioned validator set plus set version | ADR-0051 §"Validator and deployment model" |
| `dedup` | Bounded FIFO "recently seen" set of envelope content ids | ADR-0051 §"Message contract" |
| `fanout` | Deterministic, rotating selection of up to `fanout` peers, excluding the sender | ADR-0051 §"Validator and deployment model" |
| `queue` | Bounded inbound FIFO with a typed overflow refusal | ADR-0051 §"Lifecycle"; CONTRACT I11 |
| `trace` | Append-only decision log and a pure replay to a derived digest | ADR-0051 §"State contract and replay"; CONTRACT I9 |
| `kernel` | Composition: fixed check order, typed refusals, one pure `handle` | ADR-0051 |

## Protocol contract

`GossipKernel::handle(&envelope)` applies checks in a fixed order and records every outcome. Every
refusal changes no state (fail closed, CONTRACT I3):

1. **Envelope validation** — `envelope::validate` returns a deterministic id or a typed rejection
   (`UnsupportedSchema`, `UnknownKind`, `SenderEmpty`, `SetVersionEmpty`, `SequenceZero`,
   `PayloadEmpty`, `PayloadTooLarge`, `EnvelopeTooLarge`, `DigestMismatch`) checked in that order.
2. **Known peer** — the sender must be a member of the validator set, else `UnknownPeer`.
3. **Set version** — `envelope.set_version` must equal the set version the node holds, else
   `SetVersionMismatch { expected, got }`.
4. **Dedup** — a content id already seen (within the bounded FIFO) is `Duplicate`.
5. **Bounded queue** — a full inbound queue is `QueueFull { capacity }`; the refused id is not
   marked seen, so a later retry after draining is admitted.

On success the message is queued, the id is marked seen, fanout is selected (sender excluded;
rotating by a caller-visible cursor) and an `Accepted` event with its fanout is appended to the
trace. `DeliveryTrace::replay` folds the trace into accepted ids, refusal records, fanout targets
and a deterministic FNV-1a digest: identical traces replay identically, and a dropped, reordered or
altered event changes the digest.

## Parameters, not literals

ADR-0051 is **Proposed**: it recommends a single-operator shape (one node) and a three-node
consortium, but accepts no concrete set size or quorum. This crate therefore takes the validator
set, its maximum size, the set version, the dedup capacity, the queue capacity, the fanout count and
the envelope limits as caller parameters. No number is hardcoded (CONTRACT I7).

## Explicitly out of scope

- **Transport:** no sockets, listeners, connections, async runtime or network I/O; no message is
  actually sent to a peer (`fanout` only selects ids).
- **Peer discovery and membership changes:** the set is operator-provisioned; a new set version is
  an explicit operator action, not a dynamic join/leave protocol.
- **Handshake and authentication:** opaque ids are labels, not authenticated identities; node
  authentication needs a separate ADR. The content id is deterministic identity and in-process
  tamper evidence within a trusted operator group — it is **not** a signature or a security boundary.
- **Consensus:** fork detection, leader election, quorum certificates, view changes and BFT are
  designed in ADR-0051 but not implemented here (see `ezchain-consensus` for the pure tape kernel).
- **Persistence:** nothing is written to disk or a database.

## Live money and keys remain owner-blocked

These kernels carry no private keys, produce no signatures, contain no wallet addresses, do no money
arithmetic, write no ledger entries and expose no execution path. A protocol node cannot arm live
mode, reach the executor, move funds or alter the ledger. The Python ledger stays the only
accountant (CONTRACT I1) and live execution stays owner-blocked (ADR-0026, ADR-0035, ADR-0050
clause 3, ADR-0051).
