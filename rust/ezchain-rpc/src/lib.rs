//! Rust edge kernels for ezCHAIN.
//!
//! This crate is the beginning of the Rust RPC server. Each module is a port of a Python
//! original with recorded behavioural parity (ADR-0049/0050). The Python implementation
//! remains the oracle until the whole surface has passed its gate.

#![forbid(unsafe_code)]

pub mod method;
pub mod scopes;
