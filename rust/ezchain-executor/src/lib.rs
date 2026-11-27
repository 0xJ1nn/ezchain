//! Pure executor-boundary kernels of ezCHAIN.
//!
//! Every module here is a port of a Python original under the ADR-0049/0050 parity rule:
//! the recorded cases of the original must match before the port is complete. The Python
//! implementation stays the oracle. Scope is deliberately narrow:
//!
//! - [`kill_switch`] — the operator switch read, its fail-closed read failure and the
//!   entry-only refusal the pipeline applies to buys.
//! - [`gates`] — the ordered gate tree of `ExecutionPipeline` (four entry-only kill-switch
//!   reads, unknown-risk and missing/stale-quote refusals), its typed refusal codes and
//!   the idempotency key-to-payload conflict rule.
//! - [`binding`] — the "what is signed is what was approved" field comparison.
//! - [`receipt`] — the pure checks a venue receipt must pass before it can be journaled.
//! - [`state_machine`] — the typed `Intent → RiskDecision → Quote → Order → Fill →
//!   Journal` state sequence and its early-refusal transitions, built by sequencing the
//!   [`gates`] and [`receipt`] kernels with no I/O, keys, signing or money arithmetic.
//!
//! What is deliberately *not* ported: SHA-256 and JSON wire encoding/decoding (they need
//! a crypto/JSON dependency; implementing either by hand at a money boundary is not safe),
//! every `Decimal` money arithmetic path (`money.py`, journal construction, sizing and
//! risk decisions — `rust_decimal` cannot reproduce `money.py` precision), and the async
//! `ExecutionPipeline` itself (venue I/O, key use, journal sink). See the crate README.
//!
//! No I/O, no network, no key material, no external dependency.

#![forbid(unsafe_code)]

pub mod binding;
pub mod gates;
pub mod kill_switch;
pub mod receipt;
pub mod state_machine;

/// The side of an intent. Python carries this as the `"buy"`/`"sell"` string; the typed
/// boundary admits only these two values.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Side {
    Buy,
    Sell,
}

impl Side {
    /// The stable spelling of the side as the Python wire domain stores it.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Buy => "buy",
            Self::Sell => "sell",
        }
    }
}
