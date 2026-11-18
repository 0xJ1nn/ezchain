//! Recorded cases for the pure pipeline state machine. The Python original
//! (`services/executor/src/ezchain_executor/pipeline.py` and
//! `tests/executor/test_pipeline.py`) is the oracle; every row of its transition/refusal
//! table gets a case here. Comments name the Python witness.

use ezchain_executor::gates::{
    self, IdempotencyDecision, PayloadFingerprint, RefusalCode, RiskVerdict, StageEvent,
};
use ezchain_executor::receipt::FillStatus;
use ezchain_executor::state_machine::{
    drive, Gate, ReceiptVerdict, Run, RunInputs, Stage, Terminal, Transition,
};

use Stage::{Fill, Intent, Journal, Order as O, Quote as Q, RiskDecision as R};

// --- Readers ----------------------------------------------------------------

fn disarmed() -> impl FnMut() -> Result<bool, &'static str> {
    || Ok(false)
}

fn armed() -> impl FnMut() -> Result<bool, &'static str> {
    || Ok(true)
}

fn unreadable() -> impl FnMut() -> Result<bool, &'static str> {
    || Err("flags down")
}

fn reads<'a>(
    values: &'a [Result<bool, &'static str>],
) -> impl FnMut() -> Result<bool, &'static str> + 'a {
    let mut iter = values.iter().cloned();
    move || {
        iter.next()
            .expect("kill switch read more often than scripted")
    }
}

const P_INTENT: &[Stage] = &[Intent];
const P_RISK: &[Stage] = &[Intent, R];
const P_QUOTE: &[Stage] = &[Intent, R, Q];
const P_ORDER: &[Stage] = &[Intent, R, Q, O];
const P_FILL: &[Stage] = &[Intent, R, Q, O, Fill];

fn assert_refused(run: &Run, gate: Gate, code: RefusalCode, path: &[Stage]) {
    assert_eq!(run.terminal, Terminal::Refused { gate, code });
    assert_eq!(run.path.as_slice(), path);
    assert_eq!(run.refused_code(), Some(code));
    assert!(
        run.transitions
            .ends_with(&[Transition::Refuse { gate, code }]),
        "last transition must be the refusal at {gate:?}"
    );
}

/// Extract the event trace from a `gates::evaluate` outcome (either branch carries it).
fn events(outcome: gates::GateOutcome) -> Vec<StageEvent> {
    match outcome {
        gates::GateOutcome::Proceed { events } | gates::GateOutcome::Refused { events, .. } => {
            events
        }
    }
}

// --- The refusal vocabulary --------------------------------------------------

#[test]
fn every_refusal_code_is_the_exact_python_string_and_round_trips() {
    let codes = [
        (RefusalCode::MissingRiskInputs, "MISSING_RISK_INPUTS"),
        (RefusalCode::RiskDenied, "RISK_DENIED"),
        (RefusalCode::PriceUnknown, "PRICE_UNKNOWN"),
        (RefusalCode::QuoteStale, "QUOTE_STALE"),
        (RefusalCode::QuoteTimeout, "QUOTE_TIMEOUT"),
        (RefusalCode::NoQuote, "NO_QUOTE"),
        (RefusalCode::NoRoute, "NO_ROUTE"),
        (RefusalCode::NoSellRoute, "NO_SELL_ROUTE"),
        (RefusalCode::ExitNoRoute, "EXIT_NO_ROUTE"),
        (RefusalCode::QuoteSizeMismatch, "QUOTE_SIZE_MISMATCH"),
        (RefusalCode::KillSwitchActive, "KILL_SWITCH_ACTIVE"),
        (
            RefusalCode::KillSwitchUnavailable,
            "KILL_SWITCH_UNAVAILABLE",
        ),
        (RefusalCode::PositionChanged, "POSITION_CHANGED"),
        (RefusalCode::IdempotencyConflict, "IDEMPOTENCY_CONFLICT"),
        (
            RefusalCode::AutomationConsentRequired,
            "AUTOMATION_CONSENT_REQUIRED",
        ),
        (RefusalCode::InsufficientPosition, "INSUFFICIENT_POSITION"),
        (RefusalCode::InvalidAmount, "INVALID_AMOUNT"),
        (
            RefusalCode::SigningBoundaryRequired,
            "SIGNING_BOUNDARY_REQUIRED",
        ),
        (
            RefusalCode::VaultBoundaryRequired,
            "VAULT_BOUNDARY_REQUIRED",
        ),
        (RefusalCode::CustodyOwnerMismatch, "CUSTODY_OWNER_MISMATCH"),
        (
            RefusalCode::TransactionNotApprovable,
            "TRANSACTION_NOT_APPROVABLE",
        ),
        (
            RefusalCode::TransactionBuildFailed,
            "TRANSACTION_BUILD_FAILED",
        ),
        (
            RefusalCode::TransactionSignFailed,
            "TRANSACTION_SIGN_FAILED",
        ),
        (RefusalCode::ReconciliationFailed, "RECONCILIATION_FAILED"),
        (RefusalCode::Timeout, "TIMEOUT"),
        (RefusalCode::VenueRejected, "VENUE_REJECTED"),
        (RefusalCode::VenueError, "VENUE_ERROR"),
        (RefusalCode::UnbookableReceipt, "UNBOOKABLE_RECEIPT"),
        (RefusalCode::ReceiptExceedsOrder, "RECEIPT_EXCEEDS_ORDER"),
        (
            RefusalCode::TransactionDigestMismatch,
            "TRANSACTION_DIGEST_MISMATCH",
        ),
        (RefusalCode::JournalFailed, "JOURNAL_FAILED"),
    ];
    for (code, text) in codes {
        assert_eq!(code.as_str(), text);
        assert_eq!(RefusalCode::from_code(text), Some(code));
    }
    assert_eq!(RefusalCode::from_code("NOT_A_CODE"), None);
}

// --- The typed state sequence ------------------------------------------------

#[test]
fn the_six_states_are_the_one_contract_path_in_order() {
    assert_eq!(Stage::SEQUENCE, [Intent, R, Q, O, Fill, Journal]);
    for (index, stage) in Stage::SEQUENCE.iter().enumerate() {
        assert_eq!(stage.index(), index);
        assert_eq!(stage.next(), Stage::SEQUENCE.get(index + 1).copied());
    }
    assert_eq!(Journal.next(), None);
    assert_eq!(Intent.as_str(), "intent");
    assert_eq!(R.as_str(), "risk_decision");
    assert_eq!(Q.as_str(), "quote");
    assert_eq!(O.as_str(), "order");
    assert_eq!(Fill.as_str(), "fill");
    assert_eq!(Journal.as_str(), "journal");
}

#[test]
fn every_gate_names_the_state_it_stops_in() {
    assert_eq!(Gate::Idempotency.stage(), Intent);
    assert_eq!(Gate::Consent.stage(), Intent);
    assert_eq!(Gate::Sizing.stage(), Intent);
    assert_eq!(Gate::Risk.stage(), R);
    assert_eq!(Gate::KillBeforeVenue.stage(), R);
    assert_eq!(Gate::Quote.stage(), Q);
    assert_eq!(Gate::KillAfterQuote.stage(), Q);
    assert_eq!(Gate::ReRisk.stage(), Q);
    assert_eq!(Gate::OrderSize.stage(), O);
    assert_eq!(Gate::PositionRebound.stage(), O);
    assert_eq!(Gate::Sign.stage(), O);
    assert_eq!(Gate::PreSubmitKill.stage(), O);
    assert_eq!(Gate::BroadcastClaim.stage(), O);
    assert_eq!(Gate::Submit.stage(), Fill);
    assert_eq!(Gate::Receipt.stage(), Fill);
    assert_eq!(Gate::Journal.stage(), Journal);
}

// --- Happy path: all six states, four entry reads ----------------------------

#[test]
fn a_fill_walks_all_six_states_and_journals_the_receipt() {
    // test_a_fill_walks_every_stage_and_journals_the_receipt.
    let run = drive(&RunInputs::buying(), disarmed());
    assert_eq!(run.idempotency, IdempotencyDecision::Fresh);
    assert_eq!(run.stages(), &[Intent, R, Q, O, Fill, Journal]);
    assert_eq!(
        run.transitions,
        Stage::SEQUENCE.map(|stage| Transition::Enter { stage })
    );
    assert_eq!(run.terminal, Terminal::Journaled { partial: false });
    assert_eq!(run.kill_read_count(), 4);
    assert!(run.cacheable);
    assert_eq!(
        run.events,
        vec![
            StageEvent::KillSwitchRead,
            StageEvent::Quote,
            StageEvent::KillSwitchRead,
            StageEvent::Prepare,
            StageEvent::KillSwitchRead,
            StageEvent::Sign,
            StageEvent::KillSwitchRead,
            StageEvent::Submit,
        ]
    );
}

#[test]
fn a_partial_fill_is_marked_partial_at_the_journal() {
    // test_a_partial_fill_with_a_repeating_decimal_notional_is_journaled.
    let mut inputs = RunInputs::buying();
    if let Ok(sub) = &mut inputs.submit {
        sub.status = FillStatus::Partial;
    }
    let run = drive(&inputs, disarmed());
    assert_eq!(run.terminal, Terminal::Journaled { partial: true });
    assert_eq!(run.stages().len(), 6);
    assert!(run.cacheable);
}

#[test]
fn an_unsigned_paper_buy_has_only_three_entry_reads() {
    let mut inputs = RunInputs::buying();
    inputs.signing = gates::SigningPath::UnsignedPaper;
    let run = drive(&inputs, disarmed());
    assert_eq!(run.kill_read_count(), 3);
    assert_eq!(
        run.events,
        vec![
            StageEvent::KillSwitchRead,
            StageEvent::Quote,
            StageEvent::KillSwitchRead,
            StageEvent::KillSwitchRead,
            StageEvent::Submit,
        ]
    );
}

#[test]
fn a_reduce_only_exit_reads_the_switch_zero_times_even_when_armed() {
    // test_an_armed_switch_settles_an_exit_and_refuses_an_entry_in_the_same_pass and
    // test_an_exit_armed_between_quote_and_sign_still_signs_and_settles.
    let run = drive(&RunInputs::exiting(), armed());
    assert_eq!(run.kill_read_count(), 0);
    assert_eq!(run.terminal, Terminal::Journaled { partial: false });
    assert_eq!(
        run.events,
        vec![
            StageEvent::Quote,
            StageEvent::Prepare,
            StageEvent::Sign,
            StageEvent::Submit
        ]
    );
}

// --- Idempotency: conflict vs replay; cache only on order --------------------

#[test]
fn a_new_key_is_fresh_and_runs() {
    let run = drive(&RunInputs::buying(), disarmed());
    assert_eq!(run.idempotency, IdempotencyDecision::Fresh);
}

#[test]
fn the_same_key_and_payload_replays_the_cached_result_without_running() {
    // test_a_duplicate_intent_yields_one_order_and_one_fill.
    let mut inputs = RunInputs::buying();
    inputs.prior_fingerprint = Some(PayloadFingerprint::new("fp-buy"));
    let run = drive(&inputs, armed()); // reader is never consulted
    assert_eq!(run.idempotency, IdempotencyDecision::Duplicate);
    assert_eq!(run.terminal, Terminal::Replay);
    assert!(run.stages().is_empty());
    assert!(run.events.is_empty());
    assert_eq!(run.kill_read_count(), 0);
    assert!(!run.cacheable); // a replay inserts nothing into the cache
}

#[test]
fn a_reused_key_with_a_different_payload_is_a_conflict_not_a_replay() {
    // test_a_reused_key_with_a_different_payload_is_a_conflict ($100 then $900).
    let mut inputs = RunInputs::buying();
    inputs.prior_fingerprint = Some(PayloadFingerprint::new("fp-buy-900"));
    let run = drive(&inputs, armed());
    assert_eq!(run.idempotency, IdempotencyDecision::Conflict);
    assert_refused(
        &run,
        Gate::Idempotency,
        RefusalCode::IdempotencyConflict,
        &[],
    );
    assert_eq!(run.kill_read_count(), 0);
    assert!(!run.cacheable);
}

// --- Stage 0/1: consent and sizing -------------------------------------------

#[test]
fn an_unattended_intent_without_consent_refuses_before_anything_is_sized() {
    let mut inputs = RunInputs::buying();
    inputs.consent = Err(RefusalCode::AutomationConsentRequired);
    let run = drive(&inputs, armed());
    assert_refused(
        &run,
        Gate::Consent,
        RefusalCode::AutomationConsentRequired,
        &[],
    );
    assert_eq!(run.kill_read_count(), 0);
}

#[test]
fn sizing_refusals_stop_before_risk_and_are_not_cached() {
    for code in [
        RefusalCode::InsufficientPosition, // test_a_sell_without_a_position_is_rejected
        RefusalCode::PriceUnknown, // test_a_sell_without_a_mark_and_no_quantity_fails_closed
        RefusalCode::InvalidAmount,
    ] {
        let mut inputs = RunInputs::exiting();
        inputs.sizing = Err(code);
        let run = drive(&inputs, disarmed());
        assert_refused(&run, Gate::Sizing, code, &[]);
        assert_eq!(run.kill_read_count(), 0);
        assert!(!run.cacheable);
    }
}

// --- Stage 2: risk ------------------------------------------------------------

#[test]
fn a_missing_snapshot_denies_the_buy_before_any_read() {
    // test_a_missing_snapshot_denies_a_buy.
    let mut inputs = RunInputs::buying();
    inputs.pre_quote_risk = RiskVerdict::Unknown;
    let run = drive(&inputs, armed()); // never consulted
    assert_refused(&run, Gate::Risk, RefusalCode::MissingRiskInputs, P_INTENT);
    assert_eq!(run.kill_read_count(), 0);
}

#[test]
fn an_unknown_buy_mark_denies_before_the_venue() {
    // test_an_unknown_buy_mark_is_denied_before_the_venue.
    let mut inputs = RunInputs::buying();
    inputs.pre_quote_risk = RiskVerdict::Denied(RefusalCode::PriceUnknown);
    let run = drive(&inputs, armed());
    assert_refused(&run, Gate::Risk, RefusalCode::PriceUnknown, P_INTENT);
    assert_eq!(run.kill_read_count(), 0);
}

// --- Stages 3/4.5/6.7/6.8: the four entry-only kill reads --------------------

#[test]
fn an_arm_before_venue_contact_refuses_before_the_quote() {
    // test_the_kill_switch_stops_execution_before_the_venue (quote_calls == 0).
    let run = drive(&RunInputs::buying(), armed());
    assert_refused(
        &run,
        Gate::KillBeforeVenue,
        RefusalCode::KillSwitchActive,
        P_RISK,
    );
    assert_eq!(run.kill_read_count(), 1);
    assert!(!run.events.contains(&StageEvent::Quote));
}

#[test]
fn an_unreadable_switch_before_venue_contact_fails_closed() {
    // test_an_unreadable_kill_switch_fails_closed.
    let run = drive(&RunInputs::buying(), unreadable());
    assert_refused(
        &run,
        Gate::KillBeforeVenue,
        RefusalCode::KillSwitchUnavailable,
        P_RISK,
    );
}

#[test]
fn an_arm_after_the_quote_refuses_before_sign() {
    // test_the_kill_switch_is_rechecked_after_the_quote (ArmingVenue).
    let run = drive(&RunInputs::buying(), reads(&[Ok(false), Ok(true)]));
    assert_refused(
        &run,
        Gate::KillAfterQuote,
        RefusalCode::KillSwitchActive,
        P_QUOTE,
    );
    assert_eq!(run.kill_read_count(), 2);
    assert!(!run.events.contains(&StageEvent::Prepare));
}

#[test]
fn an_unreadable_switch_after_the_quote_refuses() {
    let run = drive(&RunInputs::buying(), reads(&[Ok(false), Err("flags down")]));
    assert_refused(
        &run,
        Gate::KillAfterQuote,
        RefusalCode::KillSwitchUnavailable,
        P_QUOTE,
    );
}

#[test]
fn an_arm_at_sign_time_refuses_before_the_key_is_touched() {
    // test_an_arm_between_quote_and_sign_refuses_before_the_key_is_touched.
    let run = drive(
        &RunInputs::buying(),
        reads(&[Ok(false), Ok(false), Ok(true)]),
    );
    assert_refused(&run, Gate::Sign, RefusalCode::KillSwitchActive, P_ORDER);
    assert_eq!(run.kill_read_count(), 3);
    assert!(!run.events.contains(&StageEvent::Sign));
}

#[test]
fn an_unreadable_switch_at_sign_time_refuses_before_the_key() {
    // test_an_unreadable_switch_between_quote_and_sign_refuses_before_the_key.
    let run = drive(
        &RunInputs::buying(),
        reads(&[Ok(false), Ok(false), Err("flags down")]),
    );
    assert_refused(
        &run,
        Gate::Sign,
        RefusalCode::KillSwitchUnavailable,
        P_ORDER,
    );
}

#[test]
fn an_arm_during_signing_is_recalled_before_submit() {
    // test_a_buy_armed_during_signing_is_refused_before_the_venue_submit.
    let run = drive(
        &RunInputs::buying(),
        reads(&[Ok(false), Ok(false), Ok(false), Ok(true)]),
    );
    assert_refused(
        &run,
        Gate::PreSubmitKill,
        RefusalCode::KillSwitchActive,
        P_ORDER,
    );
    assert_eq!(run.kill_read_count(), 4);
    assert!(run.events.contains(&StageEvent::Sign));
}

#[test]
fn an_unreadable_switch_at_submit_refuses() {
    let run = drive(
        &RunInputs::buying(),
        reads(&[Ok(false), Ok(false), Ok(false), Err("flags down")]),
    );
    assert_refused(
        &run,
        Gate::PreSubmitKill,
        RefusalCode::KillSwitchUnavailable,
        P_ORDER,
    );
}

// --- Stages 4/5/6: quote, re-risk, order -------------------------------------

#[test]
fn a_quote_timeout_and_a_missing_quote_produce_no_order() {
    // _quote() timeout / None / non-executable -> no order on either side.
    let mut timed_out = RunInputs::buying();
    timed_out.quote.timed_out = true;
    let run = drive(&timed_out, reads(&[Ok(false)]));
    assert_refused(&run, Gate::Quote, RefusalCode::QuoteTimeout, P_RISK);

    let mut missing = RunInputs::exiting();
    missing.quote.present = false;
    let run = drive(&missing, armed());
    assert_refused(&run, Gate::Quote, RefusalCode::NoQuote, P_RISK);

    let mut malformed = RunInputs::buying();
    malformed.quote.executable = false;
    let run = drive(&malformed, reads(&[Ok(false)]));
    assert_refused(&run, Gate::Quote, RefusalCode::NoQuote, P_RISK);
}

#[test]
fn a_stale_quote_refuses_both_sides_at_re_risk() {
    // test_a_stale_quote_is_denied_after_the_quote_stage and the sell variant.
    let mut buy = RunInputs::buying();
    buy.post_quote_risk = RiskVerdict::Denied(RefusalCode::QuoteStale);
    let run = drive(&buy, reads(&[Ok(false), Ok(false)]));
    assert_refused(&run, Gate::ReRisk, RefusalCode::QuoteStale, P_QUOTE);
    assert_eq!(run.kill_read_count(), 2);

    let mut sell = RunInputs::exiting();
    sell.post_quote_risk = RiskVerdict::Denied(RefusalCode::QuoteStale);
    let run = drive(&sell, armed());
    assert_refused(&run, Gate::ReRisk, RefusalCode::QuoteStale, P_QUOTE);
    assert_eq!(run.kill_read_count(), 0); // exits never consult the switch
}

#[test]
fn an_unknown_post_quote_verdict_refuses_after_the_quote() {
    let mut inputs = RunInputs::buying();
    inputs.post_quote_risk = RiskVerdict::Unknown;
    let run = drive(&inputs, reads(&[Ok(false), Ok(false)]));
    assert_refused(&run, Gate::ReRisk, RefusalCode::MissingRiskInputs, P_QUOTE);
}

#[test]
fn no_route_uses_the_exit_specific_code_for_sells() {
    let mut buy = RunInputs::buying();
    buy.quote.route_ok = false;
    let run = drive(&buy, reads(&[Ok(false), Ok(false)]));
    assert_refused(&run, Gate::OrderSize, RefusalCode::NoRoute, P_QUOTE);

    let mut sell = RunInputs::exiting();
    sell.quote.route_ok = false;
    sell.reduce_only = false;
    let run = drive(&sell, armed());
    assert_refused(&run, Gate::OrderSize, RefusalCode::NoSellRoute, P_QUOTE);

    let mut exit = RunInputs::exiting();
    exit.quote.route_ok = false;
    let run = drive(&exit, armed());
    assert_refused(&run, Gate::OrderSize, RefusalCode::ExitNoRoute, P_QUOTE);
}

#[test]
fn a_quote_that_is_not_the_approved_size_is_refused() {
    // test_an_order_is_refused_when_the_quote_exceeds_the_approved_size and the sell twin.
    let mut buy = RunInputs::buying();
    buy.quote.size_ok = false;
    let run = drive(&buy, reads(&[Ok(false), Ok(false)]));
    assert_refused(
        &run,
        Gate::OrderSize,
        RefusalCode::QuoteSizeMismatch,
        P_QUOTE,
    );

    let mut sell = RunInputs::exiting();
    sell.quote.size_ok = false;
    let run = drive(&sell, armed());
    assert_refused(
        &run,
        Gate::OrderSize,
        RefusalCode::QuoteSizeMismatch,
        P_QUOTE,
    );
}

// --- Stage 6.5/6.7: position rebound and opaque signing refusals -------------

#[test]
fn a_sell_whose_position_shrank_is_refused_before_submit() {
    // test_a_sell_is_rebounded_to_the_held_position_before_submit.
    let mut inputs = RunInputs::exiting();
    inputs.position_rebound_ok = false;
    let run = drive(&inputs, armed());
    assert_refused(
        &run,
        Gate::PositionRebound,
        RefusalCode::PositionChanged,
        P_ORDER,
    );
    assert_eq!(run.kill_read_count(), 0);
}

#[test]
fn signing_boundary_and_custody_refusals_are_carried_opaquely_at_the_sign_gate() {
    for code in [
        RefusalCode::SigningBoundaryRequired,
        RefusalCode::VaultBoundaryRequired,
        RefusalCode::CustodyOwnerMismatch,
        RefusalCode::TransactionNotApprovable,
        RefusalCode::TransactionBuildFailed,
        RefusalCode::TransactionSignFailed,
    ] {
        let mut inputs = RunInputs::buying();
        inputs.sign_verdict = Err(code);
        let run = drive(&inputs, reads(&[Ok(false), Ok(false), Ok(false)]));
        assert_refused(&run, Gate::Sign, code, P_ORDER);
        assert_eq!(run.kill_read_count(), 3);
        assert!(!run.cacheable); // _refused_before_submit never sets order
    }
}

// --- Stage 7: claim, submit, receipt, journal --------------------------------

#[test]
fn a_failed_broadcast_claim_refuses_before_submit_and_is_not_cached() {
    let mut inputs = RunInputs::buying();
    inputs.claim_ok = false;
    let run = drive(
        &inputs,
        reads(&[Ok(false), Ok(false), Ok(false), Ok(false)]),
    );
    assert_refused(
        &run,
        Gate::BroadcastClaim,
        RefusalCode::ReconciliationFailed,
        P_ORDER,
    );
    assert_eq!(run.kill_read_count(), 4);
    assert!(!run.cacheable);
    assert!(!run.events.contains(&StageEvent::Submit));
}

#[test]
fn submit_failures_are_cached_because_an_order_may_have_reached_the_venue() {
    // test_a_venue_rejection_produces_no_journal and test_a_submit_timeout_produces_no_journal.
    for code in [
        RefusalCode::Timeout,
        RefusalCode::VenueRejected,
        RefusalCode::VenueError,
    ] {
        let mut inputs = RunInputs::buying();
        inputs.submit = Err(code);
        let run = drive(
            &inputs,
            reads(&[Ok(false), Ok(false), Ok(false), Ok(false)]),
        );
        assert_refused(&run, Gate::Submit, code, P_ORDER);
        assert_eq!(run.kill_read_count(), 4);
        assert!(run.events.ends_with(&[StageEvent::Submit]));
        assert!(run.cacheable);
    }
}

#[test]
fn receipt_failures_are_cached_and_never_reach_the_journal() {
    // test_an_unbookable_receipt_is_refused and test_a_receipt_larger_than_the_order.
    fn with(sub: ReceiptVerdict) -> RunInputs {
        let mut inputs = RunInputs::buying();
        inputs.submit = Ok(sub);
        inputs
    }

    let mut zero = match RunInputs::buying().submit {
        Ok(sub) => sub,
        Err(_) => unreachable!(),
    };
    zero.quantity = ezchain_executor::binding::BoundAmount::parse("0").unwrap();
    let run = drive(
        &with(zero),
        reads(&[Ok(false), Ok(false), Ok(false), Ok(false)]),
    );
    assert_refused(&run, Gate::Receipt, RefusalCode::UnbookableReceipt, P_ORDER);
    assert!(run.cacheable);

    let mut over = match RunInputs::buying().submit {
        Ok(sub) => sub,
        Err(_) => unreachable!(),
    };
    over.quantity = ezchain_executor::binding::BoundAmount::parse("51").unwrap();
    let run = drive(
        &with(over),
        reads(&[Ok(false), Ok(false), Ok(false), Ok(false)]),
    );
    assert_refused(
        &run,
        Gate::Receipt,
        RefusalCode::ReceiptExceedsOrder,
        P_ORDER,
    );
    assert!(run.cacheable);

    let mut mismatch = match RunInputs::buying().submit {
        Ok(sub) => sub,
        Err(_) => unreachable!(),
    };
    mismatch.receipt_digest = Some("another-payload".to_owned());
    let run = drive(
        &with(mismatch),
        reads(&[Ok(false), Ok(false), Ok(false), Ok(false)]),
    );
    assert_refused(
        &run,
        Gate::Receipt,
        RefusalCode::TransactionDigestMismatch,
        P_ORDER,
    );
    assert!(run.cacheable);
}

#[test]
fn a_journal_failure_is_refused_after_a_fill_and_is_cached() {
    // test_a_journal_failure_is_reported_and_not_hidden.
    let mut inputs = RunInputs::buying();
    inputs.journal = Err(RefusalCode::JournalFailed);
    let run = drive(
        &inputs,
        reads(&[Ok(false), Ok(false), Ok(false), Ok(false)]),
    );
    assert_refused(&run, Gate::Journal, RefusalCode::JournalFailed, P_FILL);
    assert_eq!(run.stages().len(), 5); // fill produced, journal not
    assert!(run.cacheable);
}

// --- Cache-only-on-order-produced --------------------------------------------

#[test]
fn cacheable_iff_the_result_carried_an_order() {
    // `_should_cache(result)` is `result.order is not None`. Every refusal before the
    // submit leaves no order; submit/receipt/journal results carry one.
    let cases: Vec<(Run, bool)> = vec![
        (
            drive(
                &{
                    let mut i = RunInputs::buying();
                    i.consent = Err(RefusalCode::AutomationConsentRequired);
                    i
                },
                disarmed(),
            ),
            false,
        ),
        (
            drive(
                &{
                    let mut i = RunInputs::buying();
                    i.pre_quote_risk = RiskVerdict::Unknown;
                    i
                },
                disarmed(),
            ),
            false,
        ),
        (drive(&RunInputs::buying(), armed()), false),
        (
            drive(
                &{
                    let mut i = RunInputs::buying();
                    i.quote.present = false;
                    i
                },
                disarmed(),
            ),
            false,
        ),
        (
            drive(
                &{
                    let mut i = RunInputs::buying();
                    i.quote.size_ok = false;
                    i
                },
                disarmed(),
            ),
            false,
        ),
        (
            drive(
                &{
                    let mut i = RunInputs::exiting();
                    i.position_rebound_ok = false;
                    i
                },
                disarmed(),
            ),
            false,
        ),
        (
            drive(
                &{
                    let mut i = RunInputs::buying();
                    i.claim_ok = false;
                    i
                },
                disarmed(),
            ),
            false,
        ),
        (
            drive(
                &{
                    let mut i = RunInputs::buying();
                    i.submit = Err(RefusalCode::Timeout);
                    i
                },
                disarmed(),
            ),
            true,
        ),
        (
            drive(
                &{
                    let mut i = RunInputs::buying();
                    i.journal = Err(RefusalCode::JournalFailed);
                    i
                },
                disarmed(),
            ),
            true,
        ),
        (drive(&RunInputs::buying(), disarmed()), true),
    ];
    for (run, expected) in cases {
        assert_eq!(run.cacheable, expected, "gate {:?}", run.terminal);
        assert_eq!(gates::should_cache(run.cacheable), run.cacheable);
        // A cacheable result always produced an Order state on the way.
        if run.cacheable {
            assert!(
                run.stages().contains(&O),
                "cacheable run must have an order"
            );
        }
    }
}

// --- Cross-check: the typed machine matches gates::evaluate's gate tree ------

#[test]
fn the_state_machine_gate_trace_matches_the_canonical_gate_tree() {
    // gates::evaluate is the existing ordered gate kernel. For equivalent inputs the
    // typed machine must produce the exact same StageEvent trace, so the two cannot drift.
    let base = gates::GateInputs::buying();
    let canonical = gates::evaluate(&base, || Ok::<bool, ()>(false));
    let via_machine = drive(&RunInputs::buying(), disarmed());
    assert_eq!(via_machine.events, events(canonical));

    let exit_base = gates::GateInputs::exiting();
    let canonical_exit = gates::evaluate(&exit_base, || Ok::<bool, ()>(true));
    let via_exit = drive(&RunInputs::exiting(), armed());
    assert_eq!(via_exit.events, events(canonical_exit));

    let mut unsigned = gates::GateInputs::buying();
    unsigned.signing = gates::SigningPath::UnsignedPaper;
    let canonical_unsigned = gates::evaluate(&unsigned, || Ok::<bool, ()>(false));
    let mut unsigned_inputs = RunInputs::buying();
    unsigned_inputs.signing = gates::SigningPath::UnsignedPaper;
    let via_unsigned = drive(&unsigned_inputs, disarmed());
    assert_eq!(via_unsigned.events, events(canonical_unsigned));
}
