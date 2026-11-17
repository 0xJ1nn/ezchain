#![forbid(unsafe_code)]
//! Pure, deterministic consensus/validation kernels for ezCHAIN.
//!
//! ADR-0051 (`docs/DECISIONS.md`) defines a permissioned replicated log. This crate is its
//! state-transition and replay kernel: a deterministic projection of an ordered tape of recorded
//! events over a non-monetary string register. It performs no network I/O, holds no keys,
//! produces no signatures, runs no BFT algorithm, moves no money and writes no ledger entries.
//! Gossip, leader election, quorum certificates and persistence are designed in ADR-0051 and
//! deliberately not built here.
//!
//! The written contract in ADR-0051 is the oracle for the tests in [`tape`], because this is new
//! capability with no Python original to diff against (ADR-0050).

pub mod tape;
