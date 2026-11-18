//! Parity port of the scheduler's pure interval gate.
//!
//! The Python original is the behavioural oracle:
//! `services/engine/src/ezchain_engine/engine.py::should_run`. It is deterministic and
//! moves no money. Its only state is the caller's last-run instant, the current instant and
//! the strategy interval; it does no clock I/O. Every branch and boundary is recorded in
//! `tests/due_parity.rs` with the exact result the Python function produces (recorded from
//! the real function, not from a rewritten copy). ADR-0049/0050 parity gate: those cases
//! are the oracle until Rust passes them.
//!
//! # Boundary representation
//!
//! Python compares `datetime` values with exact microsecond arithmetic:
//!
//! ```text
//! if last_run_at is None: return True
//! return (now - last_run_at) >= timedelta(minutes=interval)
//! ```
//!
//! Rust carries no datetime type here; the caller supplies every instant as numeric
//! `Seconds` (seconds since its own epoch) and the interval in **seconds**. For the Python
//! boundary the caller passes `interval_minutes * 60`, which is exactly
//! `timedelta(minutes=interval)`. The comparison is inclusive: exactly one interval after
//! the last decision opens the gate; a clock that moved backwards never does.
//!
//! The Python original reads neither `now` nor `interval` on the first run, so
//! [`should_run`] returns `true` for `None` regardless of the other inputs — including
//! invalid ones. There is deliberately no interval validation in this kernel: the Python
//! original has none, and validating here would change its recorded behaviour for zero and
//! negative intervals.
//!
//! # Tolerance
//!
//! [`should_run`] takes an explicit, non-negative `tolerance` so callers whose arithmetic
//! is lossy floating point can treat a boundary value as exactly on the boundary. It
//! widens only the inclusive comparison (`elapsed + tolerance >= interval`). Callers with
//! exact seconds pass [`NO_TOLERANCE`], which mirrors Python exactly; a shortfall larger
//! than the tolerance still fails closed. [`should_run_exact`] is that zero-tolerance
//! mirror with Python's three-argument shape.
//!
//! # Deliberately left in Python
//!
//! * Datetime construction, timezone normalisation (`_aware`) and the `timedelta` conversion
//!   from minutes. This kernel is the numeric decision only; the Python control plane
//!   converts its stored instants to seconds before calling it.
//! * `strategy_interval_minutes` / `validate_strategy_params` (already ported as
//!   `strategy_params::interval_minutes`), scheduling, `strategy_runs` writes and the
//!   `force` override in `scheduler.py`. Those own I/O or business orchestration.

#![forbid(unsafe_code)]

/// Caller-supplied time in seconds. No clock is read by this kernel.
pub type Seconds = f64;

/// Zero tolerance: exact equality with the Python datetime comparison.
pub const NO_TOLERANCE: Seconds = 0.0;

/// The pure scheduler gate, with an injected current instant and comparison tolerance.
///
/// `last_run_at` is the strategy's last decision instant in seconds, or `None` for a
/// strategy that has never run. `now` is the injected current instant in seconds.
/// `interval_secs` is the cooldown in seconds (Python's `interval` minutes times 60).
/// `tolerance` must be non-negative; pass [`NO_TOLERANCE`] for exact parity.
///
/// Returns `true` on the first run and, afterwards, when
/// `now - last_run_at + tolerance >= interval_secs`. A backwards clock or a shortfall
/// beyond the tolerance fails closed (returns `false`).
pub fn should_run(
    last_run_at: Option<Seconds>,
    now: Seconds,
    interval_secs: Seconds,
    tolerance: Seconds,
) -> bool {
    match last_run_at {
        // Python's early return does not read `now` or `interval` at all.
        None => true,
        Some(last) => (now - last) + tolerance >= interval_secs,
    }
}

/// Exact three-argument mirror of Python's `should_run`: no tolerance.
pub fn should_run_exact(
    last_run_at: Option<Seconds>,
    now: Seconds,
    interval_secs: Seconds,
) -> bool {
    should_run(last_run_at, now, interval_secs, NO_TOLERANCE)
}
