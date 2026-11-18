//! Read-only **balance replay projection** from an ordered tape of ledger entries.
//!
//! This is a pure port of the projection half of
//! `packages/core-py/src/ezchain_core/ledger.py`: [`replay_balances`],
//! [`assert_balanced`], [`replay_position`] and [`realized_pnl`], plus the exact
//! account-naming rules (`cash_account`, `fee_account`, `realized_account`,
//! `position_account`) and the typed refusals the Python functions raise.
//!
//! # This crate is a projection, not an accountant
//!
//! [`crate::CONTRACT`] I1 says the ledger is the only accountant. This crate
//! does **not** write entries, balances or position rows, does not open a
//! database, file, socket or clock, and holds no keys. It folds a tape the
//! caller already has into derived values and refuses a tape that violates
//! conservation. The Python `ledger.py` remains the sole accountant and the
//! oracle for every case in `tests/parity_fixture.txt` (ADR-0049/0050/0052).
//! Because the projection is read-only, it can be called from any language
//! layer without becoming a second source of money truth.
//!
//! # Arithmetic boundary
//!
//! Entries carry their amounts as canonical text; parsing and all arithmetic
//! go through `ezchain-decimal`, whose per-operation precision is proven
//! against `money.py`. No Decimal is reimplemented here. USD minor units are
//! arbitrary-precision signed integers, matching Python's unbounded `int`.

#![forbid(unsafe_code)]

pub mod account;
pub mod entry;
pub mod error;
pub mod minor;
pub mod replay;

pub use account::{
    cash_account, fee_account, is_funding, position_account, realized_account, CASH_ACCOUNT,
    FEE_ACCOUNT, FUNDING_ACCOUNT, FUNDING_PREFIX, REALIZED_ACCOUNT,
};
pub use entry::LedgerEntry;
pub use error::ReplayError;
pub use replay::{assert_balanced, realized_pnl, replay_balances, replay_position, AccountBalance};
