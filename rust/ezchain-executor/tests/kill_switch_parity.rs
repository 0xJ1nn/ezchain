//! Recorded cases from `ezchain_executor.kill_switch` and
//! `ExecutionPipeline._kill_switch_denial` (`tests/executor/test_pipeline.py`).
//!
//! The Python original is the oracle. Every branch — disarmed, armed, unreadable, and
//! the entry-only rule that lets exits settle — gets a case here.

use ezchain_executor::kill_switch::{
    active, check, entry_gate, KillSwitchRefusal, KillSwitchUnavailable,
};
use ezchain_executor::Side;

// --- active(): Python `try: return bool(self.read())` -------------------------

#[test]
fn disarmed_read_is_false() {
    // Python: KillSwitch(read=lambda: False).active() is False.
    assert_eq!(active(|| Ok::<bool, ()>(false)), Ok(false));
}

#[test]
fn armed_read_is_true() {
    // Python: KillSwitch(read=lambda: True).active() is True.
    assert_eq!(active(|| Ok::<bool, ()>(true)), Ok(true));
}

#[test]
fn any_read_error_is_unavailable() {
    // Python catches `Exception` regardless of its type. The Rust reader's `Err` type is
    // deliberately varied to prove the cause is discarded, not matched.
    assert_eq!(
        active(|| Err::<bool, &str>("database down")),
        Err(KillSwitchUnavailable)
    );
    assert_eq!(active(|| Err::<bool, i32>(42)), Err(KillSwitchUnavailable));
    #[derive(Debug)]
    struct CustomError;
    assert_eq!(
        active(|| Err::<bool, CustomError>(CustomError)),
        Err(KillSwitchUnavailable)
    );
}

// --- check(): Python `if self.active(): raise KillSwitchActive` ----------------

#[test]
fn check_disarmed_proceeds() {
    assert_eq!(check(|| Ok::<bool, ()>(false)), Ok(()));
}

#[test]
fn check_armed_is_active() {
    assert_eq!(
        check(|| Ok::<bool, ()>(true)),
        Err(KillSwitchRefusal::Active)
    );
}

#[test]
fn check_unreadable_is_unavailable_not_off() {
    // A read failure must never be treated as "off".
    assert_eq!(
        check(|| Err::<bool, &str>("vault down")),
        Err(KillSwitchRefusal::Unavailable)
    );
}

#[test]
fn refusal_codes_are_the_stable_python_strings() {
    assert_eq!(KillSwitchRefusal::Active.code(), "KILL_SWITCH_ACTIVE");
    assert_eq!(
        KillSwitchRefusal::Unavailable.code(),
        "KILL_SWITCH_UNAVAILABLE"
    );
    assert_eq!(KillSwitchUnavailable::CODE, "KILL_SWITCH_UNAVAILABLE");
}

// --- entry_gate(): Python `if intent.side != "buy": return None` ---------------

#[test]
fn buy_is_refused_when_armed() {
    // test_the_kill_switch_stops_execution_before_the_venue
    assert_eq!(
        entry_gate(Side::Buy, || Ok::<bool, ()>(true)),
        Err(KillSwitchRefusal::Active)
    );
}

#[test]
fn buy_is_refused_when_unreadable() {
    // test_an_unreadable_kill_switch_fails_closed
    assert_eq!(
        entry_gate(Side::Buy, || Err::<bool, &str>("database down")),
        Err(KillSwitchRefusal::Unavailable)
    );
}

#[test]
fn buy_proceeds_when_disarmed() {
    assert_eq!(entry_gate(Side::Buy, || Ok::<bool, ()>(false)), Ok(()));
}

#[test]
fn a_sell_settles_even_while_armed() {
    // test_an_armed_switch_settles_an_exit_and_refuses_an_entry_in_the_same_pass
    assert_eq!(entry_gate(Side::Sell, || Ok::<bool, ()>(true)), Ok(()));
}

#[test]
fn a_sell_settles_even_when_the_switch_is_unreadable() {
    // The gate is entry-only: an unreadable state must not trap funds.
    assert_eq!(
        entry_gate(Side::Sell, || Err::<bool, &str>("database down")),
        Ok(())
    );
}

// --- freshness: Python reads on every decision --------------------------------

#[test]
fn arming_takes_effect_on_the_next_read() {
    // test_the_kill_switch_is_read_on_every_intent: first intent fills, second is denied
    // without any restart, because the reader is called afresh each time.
    let mut armed = false;
    let first = entry_gate(Side::Buy, || Ok::<bool, ()>(armed));
    armed = true;
    let second = entry_gate(Side::Buy, || Ok::<bool, ()>(armed));
    assert_eq!(first, Ok(()));
    assert_eq!(second, Err(KillSwitchRefusal::Active));
}

#[test]
fn the_reader_is_called_exactly_once_per_decision() {
    let mut calls = 0;
    let result = check(|| {
        calls += 1;
        Ok::<bool, ()>(false)
    });
    assert_eq!(result, Ok(()));
    assert_eq!(calls, 1);
}

#[test]
fn a_sell_does_not_even_read_the_switch() {
    let mut calls = 0;
    let result = entry_gate(Side::Sell, || {
        calls += 1;
        Ok::<bool, ()>(true)
    });
    assert_eq!(result, Ok(()));
    assert_eq!(calls, 0);
}
