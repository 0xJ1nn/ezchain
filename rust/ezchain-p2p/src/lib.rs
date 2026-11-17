#![forbid(unsafe_code)]
//! Pure, deterministic peer-message validation kernels for ezCHAIN.
//!
//! The design is ADR-0051 (`docs/DECISIONS.md`): a permissioned replicated log whose only
//! message kind in this slice is a tape event. This crate is the envelope-validation kernel that
//! design requires. It performs no network I/O, holds no keys, produces no signatures, moves no
//! money and writes no ledger entries. There is no dependency on a networking stack: gossip,
//! transport and peer discovery are designed in ADR-0051 but deliberately not built here.
//!
//! The written contract in ADR-0051 is the oracle for the tests in [`envelope`], because this is
//! new capability with no Python original to diff against (ADR-0050).

pub mod envelope;
