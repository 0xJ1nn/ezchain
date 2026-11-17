//! The operator kill switch, ported from `ezchain_executor.kill_switch` and the
//! entry-only gate in `ExecutionPipeline._kill_switch_denial` (`pipeline.py`).
//!
//! Python's `KillSwitch.active()` calls `bool(self.read())` inside `try/except Exception`
//! and raises `KillSwitchUnavailable` on *any* read failure. `check()` raises
//! `KillSwitchActive` when the read says armed. The pipeline then applies the check to
//! buys only: a sell is bounded by the held quantity and cannot open a short, so an armed
//! or unreadable switch must not trap an exit. Every read is fresh — arming takes effect
//! on the next read, without a restart.
//!
//! The typed boundary models the reader as `FnOnce() -> Result<bool, E>`: any `Err`
//! stands in for "Python raised" and maps to `KillSwitchUnavailable`. `bool` is the typed
//! form of Python's `bool(read())` coercion (a reader that cannot produce a boolean is a
//! type error here rather than a truthy value).

use crate::Side;

/// The fail-closed refusal a kill-switch read can produce.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KillSwitchRefusal {
    /// The operator switch is armed: `KILL_SWITCH_ACTIVE`.
    Active,
    /// The switch state could not be read: `KILL_SWITCH_UNAVAILABLE`.
    Unavailable,
}

impl KillSwitchRefusal {
    /// The stable reason code the pipeline records on the rejected order row.
    pub const fn code(self) -> &'static str {
        match self {
            Self::Active => "KILL_SWITCH_ACTIVE",
            Self::Unavailable => "KILL_SWITCH_UNAVAILABLE",
        }
    }
}

/// Mirrors `active()`: a failed read is an error, never "off".
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct KillSwitchUnavailable;

impl KillSwitchUnavailable {
    pub const CODE: &'static str = "KILL_SWITCH_UNAVAILABLE";
}

/// A fresh read of the persisted operator switch.
///
/// `Ok(true)` is armed, `Ok(false)` is disarmed and `Err(_)` is unreadable. The error type
/// is discarded exactly as Python's `except Exception` discards the cause.
pub fn active<E>(read: impl FnOnce() -> Result<bool, E>) -> Result<bool, KillSwitchUnavailable> {
    match read() {
        Ok(armed) => Ok(armed),
        Err(_) => Err(KillSwitchUnavailable),
    }
}

/// Mirrors `check()`: `Ok(())` means proceed, `Err` names the refusal.
pub fn check<E>(read: impl FnOnce() -> Result<bool, E>) -> Result<(), KillSwitchRefusal> {
    match active(read) {
        Ok(false) => Ok(()),
        Ok(true) => Err(KillSwitchRefusal::Active),
        Err(KillSwitchUnavailable) => Err(KillSwitchRefusal::Unavailable),
    }
}

/// Mirrors `ExecutionPipeline._kill_switch_denial`: every read is entry-only.
///
/// A sell — reduce-only or ordinary — is never gated here. An armed or unreadable switch
/// refuses a buy and still settles an exit, so the gate cannot trap funds.
pub fn entry_gate<E>(
    side: Side,
    read: impl FnOnce() -> Result<bool, E>,
) -> Result<(), KillSwitchRefusal> {
    if side != Side::Buy {
        return Ok(());
    }
    check(read)
}
