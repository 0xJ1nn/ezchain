//! Dependency-free arbitrary-precision decimal prototype for `money.py` parity.
//!
//! This crate exists to answer one question from ADR-0050 rule 4: can a Rust
//! value reproduce the *per-operation* precision of `packages/core-py/.../money.py`
//! exactly? It is a research prototype, not a money-path replacement. No caller
//! is changed, no money arithmetic is enabled, and only the operations listed in
//! [`README.md`](../README.md) are claimed as proven.
//!
//! The type is sign + coefficient (a decimal-digit [`BigUint`], so precision is
//! arbitrary, not fixed at 28 or 38 digits) + exponent. It is pure: no I/O, no
//! keys, no ledger writes, no dependencies and no `unsafe`.

#![forbid(unsafe_code)]

pub mod biguint;
pub mod decimal;
pub mod money;

pub use biguint::BigUint;
pub use decimal::{Decimal, ParseError, RoundMode};
pub use money::{
    compare, exact_precision, exact_product, exact_sum, floor_money, quantize_money, MoneyError,
};
