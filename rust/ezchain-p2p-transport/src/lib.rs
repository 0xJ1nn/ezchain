#![forbid(unsafe_code)]
//! Minimal P2P transport kernel for ezCHAIN.
//!
//! This crate is the network edge designed in ADR-0051 and assigned to Rust by ADR-0052: a
//! real `tokio` TCP listener/dialer that reads length-prefixed envelopes and feeds them into
//! the pure [`ezchain_p2p::kernel::GossipKernel`]. All protocol policy — validator set, set
//! version, fanout, envelope limits, dedup and queue capacities — is supplied by the caller
//! because ADR-0051 is **Proposed** and fixes none of those numbers (CONTRACT I7).
//!
//! # Scope
//!
//! This is a **node transport kernel**, not peer discovery, not an authenticated identity and
//! not a handshake. Peer ids are opaque labels supplied by the operator per connection. There
//! are no keys, no signatures, no money arithmetic, no ledger writes and no path to the
//! executor. Live money and vault keys stay owner-blocked (ADR-0026, ADR-0035, ADR-0050/0051).
//! Node authentication and real signatures remain a separate decision.
//!
//! The pure protocol kernels live in `ezchain-p2p` and stay dependency-free. This crate is the
//! only layer that touches sockets, and its single external dependency is `tokio`.

pub mod codec;
pub mod error;
pub mod node;

pub use codec::CodecError;
pub use error::TransportError;
pub use node::{Node, NodeConfig};
