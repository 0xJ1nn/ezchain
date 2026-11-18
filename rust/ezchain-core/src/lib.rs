//! Pure, deterministic kernels of the ezCHAIN core.
//!
//! Every module here is a port of a Python original. A port is complete only when the
//! recorded cases of the original match and the Rust gate is green (ADR-0049/0050).
//! I/O, configuration, keys and ledger writes stay in the Python control plane until
//! their own parity-gated migration.

pub mod append_log;
pub mod freshness;
pub mod freshness_policy;
pub mod reason_codes;
pub mod strategy_params;
