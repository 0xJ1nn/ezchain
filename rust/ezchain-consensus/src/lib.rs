#![forbid(unsafe_code)]
//! Pure, deterministic consensus/validation kernels for ezCHAIN.
//!
//! ADR-0051 (`docs/DECISIONS.md`) defines a permissioned replicated log. This crate holds its
//! pure kernels: the deterministic projection of an ordered tape of recorded events over a
//! non-monetary string register ([`tape`]) and the quorum/certificate checks that decide whether
//! a recorded tape may be committed ([`quorum`]). It performs no network I/O, holds no keys,
//! produces no signatures, verifies no tokens, runs no BFT algorithm, moves no money and writes
//! no ledger entries. Gossip, peer discovery, transport, leader election, view changes and
//! persistence remain designed-only in ADR-0051.
//!
//! Signatures in [`quorum`] are opaque caller-supplied tokens, not cryptography; node
//! authentication is a separate ADR. ADR-0051 is still **Proposed**: the validator model,
//! set-size and quorum parameters and the fork-choice rule here implement its recommendations,
//! not an accepted protocol decision.
//!
//! The written contract in ADR-0051 is the oracle for the tests, because this is new capability
//! with no Python original to diff against (ADR-0050).

pub mod quorum;
pub mod tape;
