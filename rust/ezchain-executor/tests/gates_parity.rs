//! Recorded cases from `ezchain_executor.pipeline` and the risk/quote value objects it
//! consumes. The Python implementation is the oracle; every gate branch, refusal code and
//! idempotency rule gets a case here. Comments name the Python test where one exists.

use ezchain_executor::gates::{
    classify, evaluate, order_gate, quote_stage_gate, risk_gate, should_cache, GateInputs,
    GateOutcome, IdempotencyDecision, IdempotencyStore, PayloadFingerprint, Quote, RefusalCode,
    RiskVerdict, SigningPath, StageEvent,
};
use ezchain_executor::Side;

use StageEvent::{KillSwitchRead as R, Prepare as P, Quote as Q, Sign as S, Submit as Sub};

/// A kill-switch reader that returns a scripted sequence. It panics if read more often
/// than scripted, so an extra (or missed) gate read fails the test rather than passing.
fn reads<'a>(
    values: &'a [Result<bool, &'static str>],
) -> impl FnMut() -> Result<bool, &'static str> + 'a {
    let mut iter = values.iter().cloned();
    move || {
        iter.next()
            .expect("kill switch read more often than scripted")
    }
}

fn refused(code: RefusalCode, events: &[StageEvent]) -> GateOutcome {
    GateOutcome::Refused {
        code,
        events: events.to_vec(),
    }
}

// --- Typed refusal codes -----------------------------------------------------

#[test]
fn refusal_codes_are_the_exact_python_strings() {
    // Reason codes are recorded verbatim on ExecutionResult.reason in pipeline.py.
    assert_eq!(
        RefusalCode::MissingRiskInputs.as_str(),
        "MISSING_RISK_INPUTS"
    );
    assert_eq!(RefusalCode::RiskDenied.as_str(), "RISK_DENIED");
    assert_eq!(RefusalCode::PriceUnknown.as_str(), "PRICE_UNKNOWN");
    assert_eq!(RefusalCode::QuoteStale.as_str(), "QUOTE_STALE");
    assert_eq!(RefusalCode::QuoteTimeout.as_str(), "QUOTE_TIMEOUT");
    assert_eq!(RefusalCode::NoQuote.as_str(), "NO_QUOTE");
    assert_eq!(RefusalCode::NoRoute.as_str(), "NO_ROUTE");
    assert_eq!(RefusalCode::NoSellRoute.as_str(), "NO_SELL_ROUTE");
    assert_eq!(RefusalCode::ExitNoRoute.as_str(), "EXIT_NO_ROUTE");
    assert_eq!(
        RefusalCode::QuoteSizeMismatch.as_str(),
        "QUOTE_SIZE_MISMATCH"
    );
    assert_eq!(RefusalCode::KillSwitchActive.as_str(), "KILL_SWITCH_ACTIVE");
    assert_eq!(
        RefusalCode::KillSwitchUnavailable.as_str(),
        "KILL_SWITCH_UNAVAILABLE"
    );
    assert_eq!(RefusalCode::PositionChanged.as_str(), "POSITION_CHANGED");
    assert_eq!(
        RefusalCode::IdempotencyConflict.as_str(),
        "IDEMPOTENCY_CONFLICT"
    );
}

// --- Risk gate: unknown refuses ---------------------------------------------

#[test]
fn an_allowed_verdict_proceeds() {
    assert_eq!(risk_gate(RiskVerdict::Allowed), Ok(()));
}

#[test]
fn a_denied_verdict_surfaces_its_primary_code() {
    assert_eq!(
        risk_gate(RiskVerdict::Denied(RefusalCode::PriceUnknown)),
        Err(RefusalCode::PriceUnknown)
    );
}

#[test]
fn an_unknown_verdict_refuses_with_missing_risk_inputs() {
    // risk_decision_from_intent() with no snapshot returns
    // RiskDecision(allowed=False, reasons=(MISSING_RISK_INPUTS,)); an unreachable risk
    // service is the same fail-closed state (CONTRACT I3). Unknown is never allowed/zero.
    // test_a_missing_snapshot_denies_a_buy.
    assert_eq!(
        risk_gate(RiskVerdict::Unknown),
        Err(RefusalCode::MissingRiskInputs)
    );
}

// --- Quote gates ------------------------------------------------------------

#[test]
fn an_executable_quote_passes_the_quote_stage() {
    assert_eq!(quote_stage_gate(Quote::executable()), Ok(()));
}

#[test]
fn a_missing_or_malformed_quote_produces_no_order() {
    // _quote(): a venue exception / None / _quote_is_executable False -> NO_QUOTE.
    // test_a_missing_quote_produces_no_order.
    let mut missing = Quote::executable();
    missing.present = false;
    assert_eq!(quote_stage_gate(missing), Err(RefusalCode::NoQuote));

    let mut malformed = Quote::executable();
    malformed.executable = false; // price/amount <= 0 or no source
    assert_eq!(quote_stage_gate(malformed), Err(RefusalCode::NoQuote));
}

#[test]
fn a_quote_timeout_is_a_distinct_code() {
    // _quote() catches TimeoutError -> status "timeout", reason "QUOTE_TIMEOUT".
    let mut quote = Quote::executable();
    quote.timed_out = true;
    assert_eq!(quote_stage_gate(quote), Err(RefusalCode::QuoteTimeout));
}

#[test]
fn order_gate_refuses_no_route_with_the_exit_specific_code() {
    let mut quote = Quote::executable();
    quote.route_ok = false;
    assert_eq!(
        order_gate(quote, Side::Buy, false),
        Err(RefusalCode::NoRoute)
    );
    assert_eq!(
        order_gate(quote, Side::Sell, false),
        Err(RefusalCode::NoSellRoute)
    );
    assert_eq!(
        order_gate(quote, Side::Sell, true),
        Err(RefusalCode::ExitNoRoute)
    );
}

#[test]
fn order_gate_refuses_a_quote_that_is_not_the_approved_size() {
    // test_an_order_is_refused_when_the_quote_exceeds_the_approved_size and
    // test_a_sell_is_refused_when_the_quote_exceeds_the_approved_quantity.
    let mut quote = Quote::executable();
    quote.size_ok = false;
    assert_eq!(
        order_gate(quote, Side::Buy, false),
        Err(RefusalCode::QuoteSizeMismatch)
    );
    assert_eq!(
        order_gate(quote, Side::Sell, true),
        Err(RefusalCode::QuoteSizeMismatch)
    );
}

#[test]
fn a_fully_executable_quote_passes_the_order_gate() {
    assert_eq!(order_gate(Quote::executable(), Side::Buy, false), Ok(()));
    assert_eq!(order_gate(Quote::executable(), Side::Sell, true), Ok(()));
}

// --- Ordered gate tree: the four kill-switch reads --------------------------

#[test]
fn one_signed_buy_crosses_the_switch_four_times_in_the_exact_order() {
    // test_one_buying_intent_crosses_the_switch_four_times_inside_one_intent:
    // ["read", "quote", "read", "prepare", "read", "sign", "read", "submit"].
    let outcome = evaluate(
        &GateInputs::buying(),
        reads(&[Ok(false), Ok(false), Ok(false), Ok(false)]),
    );
    assert_eq!(
        outcome,
        GateOutcome::Proceed {
            events: vec![R, Q, R, P, R, S, R, Sub],
        }
    );
}

#[test]
fn an_unsigned_paper_buy_has_no_sign_time_read() {
    // `_sign` is skipped when no boundary is configured in paper, so the sign-time read
    // (the fourth, between prepare and sign) is absent. The other three reads — before
    // venue contact, after the quote, before submit — still bracket the awaits.
    let mut inputs = GateInputs::buying();
    inputs.signing = SigningPath::UnsignedPaper;
    let outcome = evaluate(&inputs, reads(&[Ok(false), Ok(false), Ok(false)]));
    assert_eq!(
        outcome,
        GateOutcome::Proceed {
            events: vec![R, Q, R, R, Sub],
        }
    );
}

#[test]
fn a_sell_never_reads_the_switch_even_when_it_is_armed() {
    // test_an_exit_armed_between_quote_and_sign_still_signs_and_settles and
    // test_an_armed_switch_settles_an_exit_and_refuses_an_entry_in_the_same_pass.
    let mut calls = 0;
    let outcome = evaluate(&GateInputs::exiting(), || {
        calls += 1;
        Ok::<bool, ()>(true) // armed — but the reader must never be consulted
    });
    assert_eq!(calls, 0);
    assert_eq!(
        outcome,
        GateOutcome::Proceed {
            events: vec![Q, P, S, Sub],
        }
    );
}

#[test]
fn an_arm_before_venue_contact_refuses_before_the_quote() {
    // test_the_kill_switch_stops_execution_before_the_venue: quote_calls == 0.
    assert_eq!(
        evaluate(&GateInputs::buying(), reads(&[Ok(true)])),
        refused(RefusalCode::KillSwitchActive, &[R])
    );
}

#[test]
fn an_unreadable_switch_before_venue_contact_fails_closed() {
    // test_an_unreadable_kill_switch_fails_closed: a read error is never "off".
    assert_eq!(
        evaluate(&GateInputs::buying(), reads(&[Err("database down")])),
        refused(RefusalCode::KillSwitchUnavailable, &[R])
    );
}

#[test]
fn an_arm_after_the_quote_refuses_before_the_sign_time_read() {
    // test_the_kill_switch_is_rechecked_after_the_quote (ArmingVenue): the buy is
    // refused at the second read, with no prepare/sign/submit in the trace.
    assert_eq!(
        evaluate(&GateInputs::buying(), reads(&[Ok(false), Ok(true)])),
        refused(RefusalCode::KillSwitchActive, &[R, Q, R])
    );
}

#[test]
fn an_unreadable_switch_after_the_quote_refuses() {
    assert_eq!(
        evaluate(
            &GateInputs::buying(),
            reads(&[Ok(false), Err("system_flags down")])
        ),
        refused(RefusalCode::KillSwitchUnavailable, &[R, Q, R])
    );
}

#[test]
fn an_arm_at_sign_time_refuses_before_the_key_is_touched() {
    // test_an_arm_between_quote_and_sign_refuses_before_the_key_is_touched: the refusing
    // read sits after prepare and before sign; no Sign event exists in the trace.
    assert_eq!(
        evaluate(
            &GateInputs::buying(),
            reads(&[Ok(false), Ok(false), Ok(true)])
        ),
        refused(RefusalCode::KillSwitchActive, &[R, Q, R, P, R])
    );
}

#[test]
fn an_unreadable_switch_at_sign_time_refuses_before_the_key() {
    // test_an_unreadable_switch_between_quote_and_sign_refuses_before_the_key.
    assert_eq!(
        evaluate(
            &GateInputs::buying(),
            reads(&[Ok(false), Ok(false), Err("flags unreadable")])
        ),
        refused(RefusalCode::KillSwitchUnavailable, &[R, Q, R, P, R])
    );
}

#[test]
fn an_arm_during_signing_is_refused_before_the_venue_submit() {
    // test_a_buy_armed_during_signing_is_refused_before_the_venue_submit: the signature
    // was produced (Sign event present) but the fourth read recalls it before submit.
    assert_eq!(
        evaluate(
            &GateInputs::buying(),
            reads(&[Ok(false), Ok(false), Ok(false), Ok(true)])
        ),
        refused(RefusalCode::KillSwitchActive, &[R, Q, R, P, R, S, R])
    );
}

#[test]
fn each_entry_gate_makes_exactly_one_fresh_read() {
    // A scripted reader with exactly four values proves four reads; a fifth call would
    // panic. Mirrors the Python assertion that the switch is read once per gate.
    let mut calls = 0;
    let outcome = evaluate(&GateInputs::buying(), || {
        calls += 1;
        Ok::<bool, ()>(false)
    });
    assert!(matches!(outcome, GateOutcome::Proceed { .. }));
    assert_eq!(calls, 4);
}

#[test]
fn an_arm_is_never_served_from_an_earlier_released_read() {
    // test_an_armed_switch_is_never_served_from_an_earlier_released_read: the second read
    // crosses the boundary again and sees the arm; the scripted reader has no third value,
    // so a stale-value reuse would panic here rather than pass.
    assert_eq!(
        evaluate(&GateInputs::buying(), reads(&[Ok(false), Ok(true)])),
        refused(RefusalCode::KillSwitchActive, &[R, Q, R])
    );
}

// --- Risk / quote / order gates inside the ordered tree ---------------------

#[test]
fn an_unknown_pre_quote_verdict_refuses_before_any_read() {
    // test_a_missing_snapshot_denies_a_buy: no venue contact, no kill-switch read.
    let mut inputs = GateInputs::buying();
    inputs.pre_quote_risk = RiskVerdict::Unknown;
    assert_eq!(
        evaluate(&inputs, reads(&[])),
        refused(RefusalCode::MissingRiskInputs, &[])
    );
}

#[test]
fn an_unknown_buy_mark_is_denied_before_the_venue() {
    // test_an_unknown_buy_mark_is_denied_before_the_venue: risk sees PRICE_UNKNOWN at
    // stage 2, so no kill-switch read, quote call or submit happens.
    let mut inputs = GateInputs::buying();
    inputs.pre_quote_risk = RiskVerdict::Denied(RefusalCode::PriceUnknown);
    assert_eq!(
        evaluate(&inputs, reads(&[])),
        refused(RefusalCode::PriceUnknown, &[])
    );
}

#[test]
fn an_unknown_post_quote_verdict_refuses_after_the_quote() {
    let mut inputs = GateInputs::buying();
    inputs.post_quote_risk = RiskVerdict::Unknown;
    assert_eq!(
        evaluate(&inputs, reads(&[Ok(false), Ok(false)])),
        refused(RefusalCode::MissingRiskInputs, &[R, Q, R])
    );
}

#[test]
fn a_stale_quote_refuses_a_buy() {
    // test_a_stale_quote_is_denied_after_the_quote_stage: quote present, no order, no submit.
    let mut inputs = GateInputs::buying();
    inputs.post_quote_risk = RiskVerdict::Denied(RefusalCode::QuoteStale);
    assert_eq!(
        evaluate(&inputs, reads(&[Ok(false), Ok(false)])),
        refused(RefusalCode::QuoteStale, &[R, Q, R])
    );
}

#[test]
fn a_stale_quote_refuses_a_sell_too() {
    // test_a_stale_quote_is_refused_for_a_sell_with_a_snapshot: exits are not exempt from
    // the quote-freshness gate. No kill-switch read occurs on this path.
    let mut inputs = GateInputs::exiting();
    inputs.post_quote_risk = RiskVerdict::Denied(RefusalCode::QuoteStale);
    assert_eq!(
        evaluate(&inputs, reads(&[])),
        refused(RefusalCode::QuoteStale, &[Q])
    );
}

#[test]
fn a_missing_quote_refuses_both_sides() {
    // test_a_missing_quote_produces_no_order and the sell-with-no-snapshot stale-quote case.
    let mut buy = GateInputs::buying();
    buy.quote.present = false;
    assert_eq!(
        evaluate(&buy, reads(&[Ok(false)])),
        refused(RefusalCode::NoQuote, &[R, Q])
    );

    let mut sell = GateInputs::exiting();
    sell.quote.present = false;
    assert_eq!(
        evaluate(&sell, reads(&[])),
        refused(RefusalCode::NoQuote, &[Q])
    );
}

#[test]
fn a_quote_size_mismatch_refuses_before_signing() {
    // test_an_order_is_refused_when_the_quote_exceeds_the_approved_size.
    let mut inputs = GateInputs::buying();
    inputs.quote.size_ok = false;
    assert_eq!(
        evaluate(&inputs, reads(&[Ok(false), Ok(false)])),
        refused(RefusalCode::QuoteSizeMismatch, &[R, Q, R])
    );
}

#[test]
fn a_sell_is_refused_when_the_held_position_shrinks_before_submit() {
    // test_a_sell_is_rebounded_to_the_held_position_before_submit: POSITION_CHANGED after
    // pricing, before submit; no kill-switch read on an exit.
    let mut inputs = GateInputs::exiting();
    inputs.position_rebound_ok = false;
    assert_eq!(
        evaluate(&inputs, reads(&[])),
        refused(RefusalCode::PositionChanged, &[Q])
    );
}

// --- Idempotency: key bound to payload --------------------------------------

#[test]
fn a_new_key_is_fresh() {
    let fp = PayloadFingerprint::new("fp-1");
    assert_eq!(classify(None, &fp), IdempotencyDecision::Fresh);
}

#[test]
fn the_same_key_and_payload_is_a_duplicate() {
    let fp = PayloadFingerprint::new("fp-1");
    assert_eq!(classify(Some(&fp), &fp), IdempotencyDecision::Duplicate);
}

#[test]
fn a_reused_key_with_a_different_payload_is_a_conflict() {
    // test_a_reused_key_with_a_different_payload_is_a_conflict: same key, $100 then $900.
    let first = PayloadFingerprint::new("buy-100");
    let second = PayloadFingerprint::new("buy-900");
    assert_eq!(
        classify(Some(&first), &second),
        IdempotencyDecision::Conflict
    );
    assert_eq!(
        RefusalCode::IdempotencyConflict.as_str(),
        "IDEMPOTENCY_CONFLICT"
    );
}

#[test]
fn only_order_producing_results_are_cached() {
    // _should_cache: a pre-submit refusal carries no order and is retryable.
    assert!(!should_cache(false));
    assert!(should_cache(true));
}

#[test]
fn a_denial_is_not_cached_so_a_fixed_retry_runs_fresh() {
    // test_a_denial_is_not_cached_so_a_fixed_retry_runs: the denied run produces no order,
    // so nothing is stored and the retry with a good snapshot is a Fresh run.
    let fp = PayloadFingerprint::new("retry-denied");
    let mut store = IdempotencyStore::new(4).unwrap();
    let denial_had_order = false;
    if should_cache(denial_had_order) {
        store.insert("retry-denied", fp.clone(), "denied");
    }
    assert!(!store.contains("retry-denied"));
    assert_eq!(classify(None, &fp), IdempotencyDecision::Fresh);
}

#[test]
fn the_store_rejects_a_zero_bound() {
    assert!(matches!(
        IdempotencyStore::<u8>::new(0),
        Err("the idempotency store needs at least one entry")
    ));
}

#[test]
fn the_store_evicts_the_oldest_entry_when_full() {
    // test_the_idempotency_store_is_bounded_and_evicts_the_oldest.
    let mut store = IdempotencyStore::new(2).unwrap();
    for index in 0..3 {
        store.insert(
            format!("bounded-{index}"),
            PayloadFingerprint::new(format!("fp-{index}")),
            index,
        );
    }
    assert_eq!(store.len(), 2);
    assert!(!store.contains("bounded-0"));
    assert!(store.contains("bounded-1"));
    assert!(store.contains("bounded-2"));
}

#[test]
fn a_get_promotes_an_entry_so_it_is_not_evicted() {
    // DedupeStore.get() calls move_to_end, so a recently replayed key survives eviction.
    let mut store = IdempotencyStore::new(2).unwrap();
    store.insert("a", PayloadFingerprint::new("fp-a"), 1);
    store.insert("b", PayloadFingerprint::new("fp-b"), 2);
    assert!(store.get("a").is_some()); // "a" becomes most recent; "b" is now oldest
    store.insert("c", PayloadFingerprint::new("fp-c"), 3);
    assert!(!store.contains("b"));
    assert!(store.contains("a"));
    assert!(store.contains("c"));
}

#[test]
fn reinserting_a_key_updates_its_fingerprint_without_growing_the_store() {
    let mut store = IdempotencyStore::new(2).unwrap();
    store.insert("k", PayloadFingerprint::new("old"), 1);
    store.insert("k", PayloadFingerprint::new("new"), 2);
    assert_eq!(store.len(), 1);
    let (fingerprint, value) = store.get("k").unwrap();
    assert_eq!(fingerprint.as_str(), "new");
    assert_eq!(*value, 2);
}
