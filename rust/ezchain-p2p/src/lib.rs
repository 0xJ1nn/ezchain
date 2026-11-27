#![forbid(unsafe_code)]
//! Pure, deterministic peer-protocol kernels for ezCHAIN.
//!
//! The design is ADR-0051 (`docs/DECISIONS.md`): a permissioned replicated log whose only message
//! kind in this slice is a tape event. This crate implements the pure protocol kernels that design
//! requires: envelope validation ([`envelope`]), a bounded ordered validator set ([`peer`]),
//! bounded content-id deduplication ([`dedup`]), a deterministic fanout rule ([`fanout`]), a bounded
//! inbound queue ([`queue`]), a replayable delivery trace ([`trace`]) and their composition into
//! one pure kernel ([`kernel`]).
//!
//! # This is a protocol kernel, not a transport
//!
//! There are no sockets, no listeners, no async runtime and no external dependency. Nothing here
//! discovers peers, connects, handshakes, authenticates a node id, signs or persists anything, or
//! moves a byte on a network. Gossip propagation, peer discovery, transport, handshake and node
//! authentication are designed as boundaries in ADR-0051 but deliberately not built here. The
//! concrete validator set, set size, quorum threshold and fanout are caller parameters: ADR-0051 is
//! **Proposed** and decides none of them (CONTRACT I7).
//!
//! # Live money and vault keys remain owner-blocked
//!
//! These kernels carry no private keys, no signatures, no wallet addresses, no money arithmetic,
//! no ledger writes and no execution path. A protocol node cannot arm live mode, reach the
//! executor, move funds or alter the ledger. The ledger stays the only accountant (CONTRACT I1)
//! and live execution stays owner-blocked (ADR-0026, ADR-0035, ADR-0050 clause 3, ADR-0051).
//!
//! The written contract in ADR-0051 is the oracle for these tests: this is new capability with no
//! Python original to diff against (ADR-0050).

pub mod dedup;
pub mod envelope;
pub mod fanout;
pub mod kernel;
pub mod peer;
pub mod queue;
pub mod trace;
