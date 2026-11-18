//! Recorded cases for the scheduler interval gate, taken from the Python oracle
//! `services/engine/src/ezchain_engine/engine.py::should_run` and its test
//! `tests/engine/test_scheduler.py::test_should_run_normalizes_naive_and_aware_timestamps`.
//!
//! The values below were produced by calling the real Python function and translating its
//! datetime instants to seconds relative to a fixed base (2026-10-22 12:00:00 UTC). Python
//! `interval` is in minutes, so `interval_secs = interval * 60` equals the oracle's
//! `timedelta(minutes=interval)`. `true`/`false` below are the oracle's actual returns.
//! ADR-0049/0050 parity gate: these cases are the oracle until Rust passes them.

use ezchain_core::due::{should_run, should_run_exact, Seconds, NO_TOLERANCE};

struct Case {
    label: &'static str,
    last_run_at: Option<Seconds>,
    now: Seconds,
    interval_secs: Seconds,
    expected: bool,
}

/// All 27 cases recorded from the Python oracle.
const RECORDED: &[Case] = &[
    // first run: None opens the gate regardless of interval / now (Python returns before
    // reading either argument)
    Case {
        label: "first_run_none_zero_interval",
        last_run_at: None,
        now: 0.0,
        interval_secs: 0.0,
        expected: true,
    },
    Case {
        label: "first_run_none_negative_interval",
        last_run_at: None,
        now: 0.0,
        interval_secs: -300.0,
        expected: true,
    },
    Case {
        label: "first_run_none_positive_interval",
        last_run_at: None,
        now: 3600.0,
        interval_secs: 3600.0,
        expected: true,
    },
    Case {
        label: "first_run_none_clock_backwards",
        last_run_at: None,
        now: -3600.0,
        interval_secs: 3600.0,
        expected: true,
    },
    // exact boundary: elapsed == interval -> due (the gate is inclusive)
    Case {
        label: "exact_boundary_1min",
        last_run_at: Some(0.0),
        now: 60.0,
        interval_secs: 60.0,
        expected: true,
    },
    Case {
        label: "exact_boundary_60min",
        last_run_at: Some(0.0),
        now: 3600.0,
        interval_secs: 3600.0,
        expected: true,
    },
    Case {
        label: "exact_boundary_large",
        last_run_at: Some(1000.0),
        now: 605800.0,
        interval_secs: 604800.0,
        expected: true,
    },
    // just before the boundary -> not due
    Case {
        label: "just_before_1s",
        last_run_at: Some(0.0),
        now: 59.0,
        interval_secs: 60.0,
        expected: false,
    },
    Case {
        label: "just_before_60min",
        last_run_at: Some(0.0),
        now: 3599.0,
        interval_secs: 3600.0,
        expected: false,
    },
    Case {
        label: "just_before_1ms",
        last_run_at: Some(0.0),
        now: 3599.999,
        interval_secs: 3600.0,
        expected: false,
    },
    // long overdue -> due
    Case {
        label: "long_overdue",
        last_run_at: Some(0.0),
        now: 720000.0,
        interval_secs: 3600.0,
        expected: true,
    },
    Case {
        label: "overdue_by_one_second",
        last_run_at: Some(0.0),
        now: 3601.0,
        interval_secs: 3600.0,
        expected: true,
    },
    // clock moved backwards -> never due
    Case {
        label: "clock_backwards_1s",
        last_run_at: Some(3600.0),
        now: 3599.0,
        interval_secs: 3600.0,
        expected: false,
    },
    Case {
        label: "clock_backwards_full_interval",
        last_run_at: Some(3600.0),
        now: 0.0,
        interval_secs: 3600.0,
        expected: false,
    },
    Case {
        label: "clock_backwards_huge",
        last_run_at: Some(7200.0),
        now: -7200.0,
        interval_secs: 3600.0,
        expected: false,
    },
    // invalid interval: should_run does not validate; the raw comparison decides
    Case {
        label: "zero_interval_no_elapsed",
        last_run_at: Some(0.0),
        now: 0.0,
        interval_secs: 0.0,
        expected: true,
    },
    Case {
        label: "zero_interval_elapsed",
        last_run_at: Some(0.0),
        now: 1.0,
        interval_secs: 0.0,
        expected: true,
    },
    Case {
        label: "zero_interval_backwards",
        last_run_at: Some(0.0),
        now: -1.0,
        interval_secs: 0.0,
        expected: false,
    },
    Case {
        label: "negative_interval_no_elapsed",
        last_run_at: Some(0.0),
        now: 0.0,
        interval_secs: -300.0,
        expected: true,
    },
    Case {
        label: "negative_interval_elapsed",
        last_run_at: Some(0.0),
        now: 60.0,
        interval_secs: -300.0,
        expected: true,
    },
    Case {
        label: "negative_interval_backwards_small",
        last_run_at: Some(0.0),
        now: -10.0,
        interval_secs: -300.0,
        expected: true,
    },
    Case {
        label: "negative_interval_backwards_large",
        last_run_at: Some(0.0),
        now: -3600.0,
        interval_secs: -300.0,
        expected: false,
    },
    // fractional interval (Python timedelta accepts a float)
    Case {
        label: "fractional_interval_exact",
        last_run_at: Some(0.0),
        now: 90.0,
        interval_secs: 90.0,
        expected: true,
    },
    Case {
        label: "fractional_interval_just_before",
        last_run_at: Some(0.0),
        now: 89.0,
        interval_secs: 90.0,
        expected: false,
    },
    // naive/aware normalisation from test_should_run_normalizes_naive_and_aware_timestamps
    Case {
        label: "naive_last_aware_now_60",
        last_run_at: Some(0.0),
        now: 3600.0,
        interval_secs: 3600.0,
        expected: true,
    },
    Case {
        label: "naive_last_aware_now_61",
        last_run_at: Some(0.0),
        now: 3600.0,
        interval_secs: 3660.0,
        expected: false,
    },
    Case {
        label: "aware_last_earlier_now",
        last_run_at: Some(3600.0),
        now: 0.0,
        interval_secs: 3600.0,
        expected: false,
    },
];

#[test]
fn all_recorded_python_oracle_cases_match() {
    assert_eq!(RECORDED.len(), 27, "recorded case count changed");
    for case in RECORDED {
        assert_eq!(
            should_run_exact(case.last_run_at, case.now, case.interval_secs),
            case.expected,
            "exact mirror disagrees with the oracle on {}",
            case.label
        );
        assert_eq!(
            should_run(case.last_run_at, case.now, case.interval_secs, NO_TOLERANCE),
            case.expected,
            "four-argument API with NO_TOLERANCE disagrees on {}",
            case.label
        );
    }
}

#[test]
fn first_run_is_unconditionally_due() {
    // Python returns before reading `now` or `interval`.
    for interval in [-1.0, 0.0, 3600.0, f64::INFINITY] {
        for now in [-1.0, 0.0, 3600.0] {
            assert!(should_run_exact(None, now, interval));
        }
    }
}

#[test]
fn exact_boundary_is_due_and_one_second_before_is_not() {
    assert!(should_run_exact(Some(0.0), 3600.0, 3600.0));
    assert!(!should_run_exact(Some(0.0), 3599.0, 3600.0));
    // A non-zero last instant behaves the same: only the elapsed gap matters.
    assert!(should_run_exact(Some(12345.0), 12345.0 + 3600.0, 3600.0));
    assert!(!should_run_exact(Some(12345.0), 12345.0 + 3599.999, 3600.0));
}

#[test]
fn long_overdue_is_due() {
    assert!(should_run_exact(Some(0.0), 3600.0 * 200.0, 3600.0));
    assert!(should_run_exact(Some(0.0), 3601.0, 3600.0));
}

#[test]
fn backwards_clock_never_opens_the_gate() {
    assert!(!should_run_exact(Some(3600.0), 3599.0, 3600.0));
    assert!(!should_run_exact(Some(3600.0), 0.0, 3600.0));
    assert!(!should_run_exact(Some(7200.0), -7200.0, 3600.0));
}

#[test]
fn invalid_interval_follows_the_raw_python_comparison() {
    // Zero: due when now >= last, not due when the clock moved backwards.
    assert!(should_run_exact(Some(0.0), 0.0, 0.0));
    assert!(should_run_exact(Some(0.0), 1.0, 0.0));
    assert!(!should_run_exact(Some(0.0), -1.0, 0.0));
    // Negative: due unless the clock moved backwards by more than the interval magnitude.
    assert!(should_run_exact(Some(0.0), 0.0, -300.0));
    assert!(should_run_exact(Some(0.0), 60.0, -300.0));
    assert!(should_run_exact(Some(0.0), -10.0, -300.0));
    assert!(!should_run_exact(Some(0.0), -3600.0, -300.0));
}

#[test]
fn fractional_interval_matches_timedelta_minutes() {
    // 1.5 minutes == 90 seconds == timedelta(minutes=1.5).
    assert!(should_run_exact(Some(0.0), 90.0, 90.0));
    assert!(!should_run_exact(Some(0.0), 89.0, 90.0));
}

#[test]
fn tolerance_widens_only_the_inclusive_boundary() {
    // A value computed just short of the boundary by floating-point error opens the gate
    // when the shortfall is inside the tolerance.
    assert!(!should_run(Some(0.0), 3599.999, 3600.0, NO_TOLERANCE));
    assert!(should_run(Some(0.0), 3599.999, 3600.0, 0.002));
    // A genuinely earlier time stays closed when the shortfall exceeds the tolerance.
    assert!(!should_run(Some(0.0), 3599.0, 3600.0, 0.5));
    // Tolerance does not let a backwards clock open the gate.
    assert!(!should_run(Some(3600.0), 3599.0, 3600.0, 0.5));
    assert!(!should_run(Some(3600.0), 3599.0, 3600.0, 0.999_999));
}

#[test]
fn decision_is_monotonic_in_now_and_interval() {
    // Determinism check over a fixed grid (no randomness, no clock I/O): moving `now`
    // forward never closes a gate that was open, and increasing the interval never opens
    // one that was closed.
    for last in [0.0, 100.0, -100.0] {
        for interval in [0.0, 60.0, 3600.0] {
            for now in -200..401 {
                let now = now as Seconds;
                let before = should_run_exact(Some(last), now, interval);
                let after = should_run_exact(Some(last), now + 1.0, interval);
                if before {
                    assert!(
                        after,
                        "gate closed as now advanced (last={last}, interval={interval}, now={now})"
                    );
                }
                let tighter = should_run_exact(Some(last), now, interval + 1.0);
                if tighter {
                    assert!(before, "gate opened as interval grew (last={last}, interval={interval}, now={now})");
                }
            }
        }
    }
}
