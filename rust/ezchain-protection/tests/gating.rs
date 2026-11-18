//! An armed kill switch is an entry-only gate (ADR-0031/0034).
//!
//! A triggered protection arm always yields a reduce-only exit, so the gate can
//! never veto it. The Python control plane owns the persisted switch and the
//! order call; this file mirrors the direction decision in isolation.

use std::collections::BTreeMap;

use ezchain_protection::*;

fn lex(s: &str) -> DecimalLexeme {
    DecimalLexeme::parse(s).unwrap()
}

fn fired_stop_loss() -> ArmEvaluation {
    let params = ArmParams {
        kind: ArmKind::StopLoss,
        price_usd: Some(lex("0.90")),
        loss_pct: None,
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: lex("1"),
        levels: Vec::new(),
    };
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: None,
        triggered_level_indexes: &[],
        expires_at_ns: None,
    };
    let price = lex("0.85");
    let qty = lex("100");
    let cost = lex("1.00");
    let input = EvaluateInput {
        price_usd: Some(&price),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: None,
        computed_trigger_price: None,
        computed_level_triggers: &[],
    };
    evaluate_arm(&arm, &input)
}

#[test]
fn kill_switch_refuses_only_entries() {
    assert!(!entry_gate_allows(true, IntentDirection::Entry));
    assert!(entry_gate_allows(false, IntentDirection::Entry));
    assert!(entry_gate_allows(true, IntentDirection::ReduceOnlyExit));
    assert!(entry_gate_allows(false, IntentDirection::ReduceOnlyExit));
}

#[test]
fn triggered_arm_is_a_reduce_only_exit_the_kill_switch_cannot_block() {
    let evaluation = fired_stop_loss();
    assert_eq!(evaluation.action, Action::Trigger);
    assert_eq!(evaluation.reason, Reason::StopLoss);
    let direction = evaluation.direction();
    assert_eq!(direction, Some(IntentDirection::ReduceOnlyExit));
    // Armed kill switch: the exit still proceeds.
    assert!(entry_gate_allows(true, direction.unwrap()));
}

#[test]
fn held_arm_produces_no_intent() {
    // A stale mark holds the arm; no intent exists to gate either way.
    let params = ArmParams {
        kind: ArmKind::StopLoss,
        price_usd: Some(lex("0.90")),
        loss_pct: None,
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: lex("1"),
        levels: Vec::new(),
    };
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: None,
        triggered_level_indexes: &[],
        expires_at_ns: None,
    };
    let price = lex("0.85");
    let qty = lex("100");
    let cost = lex("1.00");
    let input = EvaluateInput {
        price_usd: Some(&price),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: true,
        now_ns: None,
        computed_trigger_price: None,
        computed_level_triggers: &[],
    };
    let evaluation = evaluate_arm(&arm, &input);
    assert_eq!(evaluation.reason, Reason::MarkStale);
    assert_eq!(evaluation.direction(), None);
}

#[test]
fn triggered_arm_carries_full_sell_fraction_and_replay_inputs() {
    let e = fired_stop_loss();
    assert_eq!(e.sell_fraction, lex("1"));
    // `inputs` is an exact replay map, so the decision can be reconstructed.
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("stop_loss".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
        ("price_usd".to_owned(), Some("0.85".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("position_avg_cost_usd".to_owned(), Some("1.00".to_owned())),
        ("peak_usd".to_owned(), None),
        ("trigger_price_usd".to_owned(), Some("0.90".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(e.inputs, expected);
    assert_eq!(e.level_index, None);
}
