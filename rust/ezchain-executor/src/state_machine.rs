//! The pure transaction-pipeline state machine: the one execution path
//! `Intent → RiskDecision → Quote → Order → Fill → Journal` (CONTRACT I2) expressed as a
//! typed sequence of states and transitions.
//!
//! This module is the *sequencing* layer. It owns the order of the stages and the early
//! refusals between them; it does not reimplement any gate. Every check delegates to an
//! existing kernel:
//!
//! - [`crate::gates::risk_gate`], [`crate::gates::quote_stage_gate`] and
//!   [`crate::gates::order_gate`] for the risk / quote / size checks;
//! - [`crate::kill_switch::entry_gate`] for each of the four entry-only kill-switch reads;
//! - [`crate::receipt::verify_receipt`] for the receipt bounds and digest echo;
//! - [`crate::gates::classify`] and [`crate::gates::should_cache`] for idempotency and the
//!   cache-only-on-order-produced rule.
//!
//! Money arithmetic, venue I/O, key use, signing and ledger writes stay in Python. They
//! arrive here as opaque typed records/verdicts; this crate only sequences and compares
//! them. In particular the venue quote, the signed payload digest and the confirmed
//! receipt are carried as opaque fields, never computed or moved.
//!
//! # The recorded Python transition/refusal table
//!
//! Every row is from `services/executor/src/ezchain_executor/pipeline.py` and
//! `tests/executor/test_pipeline.py`. `R` = reduce-only exit, `K` = any kill-switch read.
//! The `tests/state_machine_parity.rs` suite asserts each row against [`drive`].
//!
//! | Python stage | Gate | Refusal code | Entry/exit | Python witness |
//! |---|---|---|---|---|
//! | `run` | idempotency conflict | `IDEMPOTENCY_CONFLICT` | both | `test_a_reused_key_with_a_different_payload_is_a_conflict` |
//! | `run` | same key + payload | (replay, `duplicate=True`) | both | `test_a_duplicate_intent_yields_one_order_and_one_fill` |
//! | 0 consent | no matching automation consent | `AUTOMATION_CONSENT_REQUIRED` | both | `_automation_denial` |
//! | 1 size | no held position | `INSUFFICIENT_POSITION` | sell | `test_a_sell_without_a_position_is_rejected` |
//! | 1 size | no quantity and no mark | `PRICE_UNKNOWN` | sell | `test_a_sell_without_a_mark_and_no_quantity_fails_closed` |
//! | 1 size | non-positive size | `INVALID_AMOUNT` | sell | `_size_request` |
//! | 2 risk | missing/unknown inputs | `MISSING_RISK_INPUTS` | both | `test_a_missing_snapshot_denies_a_buy` |
//! | 2 risk | unknown buy mark | `PRICE_UNKNOWN` | buy | `test_an_unknown_buy_mark_is_denied_before_the_venue` |
//! | 3 K before venue | armed | `KILL_SWITCH_ACTIVE` | entry | `test_the_kill_switch_stops_execution_before_the_venue` |
//! | 3 K before venue | unreadable | `KILL_SWITCH_UNAVAILABLE` | entry | `test_an_unreadable_kill_switch_fails_closed` |
//! | 4 quote | timeout | `QUOTE_TIMEOUT` | both | `_quote` |
//! | 4 quote | no/malformed quote | `NO_QUOTE` | both | `test_a_missing_quote_produces_no_order` |
//! | 4.5 K after quote | armed | `KILL_SWITCH_ACTIVE` | entry | `test_the_kill_switch_is_rechecked_after_the_quote` |
//! | 5 re-risk | stale quote | `QUOTE_STALE` | both | `test_a_stale_quote_is_denied_after_the_quote_stage` |
//! | 6 order | no route / no sell route / no exit route | `NO_ROUTE` / `NO_SELL_ROUTE` / `EXIT_NO_ROUTE` | both | `order_gate` |
//! | 6 order | quote not the approved size | `QUOTE_SIZE_MISMATCH` | both | `test_an_order_is_refused_when_the_quote_exceeds_the_approved_size` |
//! | 6.5 re-bound | position shrank | `POSITION_CHANGED` | sell | `test_a_sell_is_rebounded_to_the_held_position_before_submit` |
//! | 6.7 sign-time K | armed / unreadable | `KILL_SWITCH_ACTIVE` / `KILL_SWITCH_UNAVAILABLE` | entry | `test_an_arm_between_quote_and_sign_refuses_before_the_key_is_touched` |
//! | 6.7 sign | boundary / custody / build / sign refusals | `SIGNING_BOUNDARY_REQUIRED`, `VAULT_BOUNDARY_REQUIRED`, `CUSTODY_OWNER_MISMATCH`, `TRANSACTION_*` | both | `_sign` |
//! | 6.8 K before submit | armed / unreadable | `KILL_SWITCH_ACTIVE` / `KILL_SWITCH_UNAVAILABLE` | entry | `test_a_buy_armed_during_signing_is_refused_before_the_venue_submit` |
//! | 7 claim | broadcast claim failed | `RECONCILIATION_FAILED` | both | `_run` |
//! | 7.1 submit | timeout / venue error / reject | `TIMEOUT` / `VENUE_ERROR` / `VENUE_REJECTED` | both | `test_a_submit_timeout_produces_no_journal` |
//! | 7.1 receipt | unbookable / overfill / digest echo | `UNBOOKABLE_RECEIPT` / `RECEIPT_EXCEEDS_ORDER` / `TRANSACTION_DIGEST_MISMATCH` | both | `test_an_unbookable_receipt_is_refused` |
//! | 8 journal | build/append failed | `JOURNAL_FAILED` | both | `test_a_journal_failure_is_reported_and_not_hidden` |
//! | success | full / partial fill | `filled` / `partially_filled` | both | `test_a_fill_walks_every_stage_and_journals_the_receipt` |
//!
//! Kill-switch reads: a signed buy crosses the switch four times — before venue contact,
//! after the quote, at sign time and before submit (`["read","quote","read","prepare",
//! "read","sign","read","submit"]` in
//! `test_one_buying_intent_crosses_the_switch_four_times_inside_one_intent`). An unsigned
//! paper buy has three (no sign-time read). A sell — reduce-only or ordinary — has zero:
//! an exit is bounded by the held position and cannot open a short, so an arm never traps
//! it (`test_a_sell_never_reads_the_switch_even_when_it_is_armed`).

use crate::binding::BoundAmount;
use crate::gates::{
    self, IdempotencyDecision, PayloadFingerprint, Quote, RefusalCode, RiskVerdict, SigningPath,
    StageEvent,
};
use crate::kill_switch;
use crate::receipt::{self, FillStatus};
use crate::Side;

/// The six states of the one execution path, in the order Python crosses them.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Stage {
    Intent,
    RiskDecision,
    Quote,
    Order,
    Fill,
    Journal,
}

impl Stage {
    /// The canonical ordered sequence — CONTRACT I2's one pipeline.
    pub const SEQUENCE: [Stage; 6] = [
        Self::Intent,
        Self::RiskDecision,
        Self::Quote,
        Self::Order,
        Self::Fill,
        Self::Journal,
    ];

    /// The stage's position in [`Stage::SEQUENCE`].
    pub const fn index(self) -> usize {
        match self {
            Self::Intent => 0,
            Self::RiskDecision => 1,
            Self::Quote => 2,
            Self::Order => 3,
            Self::Fill => 4,
            Self::Journal => 5,
        }
    }

    /// The stable snake-case name Python uses for the produced record.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Intent => "intent",
            Self::RiskDecision => "risk_decision",
            Self::Quote => "quote",
            Self::Order => "order",
            Self::Fill => "fill",
            Self::Journal => "journal",
        }
    }

    /// The next state on the happy path, or `None` at the terminal journal state.
    pub const fn next(self) -> Option<Self> {
        match self {
            Self::Intent => Some(Self::RiskDecision),
            Self::RiskDecision => Some(Self::Quote),
            Self::Quote => Some(Self::Order),
            Self::Order => Some(Self::Fill),
            Self::Fill => Some(Self::Journal),
            Self::Journal => None,
        }
    }
}

/// The numbered checkpoints of `ExecutionPipeline._run`. Each maps to the stage whose
/// processing owns it, so a refusal can name both the gate and the state it stopped in.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Gate {
    /// `ExecutionPipeline.run`: the key was already claimed by a different payload.
    Idempotency,
    /// Stage 0 — `_automation_denial`.
    Consent,
    /// Stage 1 — `_size_request`.
    Sizing,
    /// Stage 2 — `risk_decision_from_intent` before a quote exists.
    Risk,
    /// Stage 3 — `_kill_switch_denial` before venue contact.
    KillBeforeVenue,
    /// Stage 4 — `_quote`.
    Quote,
    /// Stage 4.5 — `_kill_switch_denial` after the quote round-trip.
    KillAfterQuote,
    /// Stage 5 — `risk_decision_from_intent` re-run with the quote.
    ReRisk,
    /// Stage 6 — `_order_from_quote` route/size checks.
    OrderSize,
    /// Stage 6.5 — the sell re-bound against the held position.
    PositionRebound,
    /// Stage 6.7 — `_sign` prepare, sign-time switch read and signature.
    Sign,
    /// Stage 6.8 — the last `_kill_switch_denial` before submit.
    PreSubmitKill,
    /// Stage 7 — the durable broadcast claim (outbox).
    BroadcastClaim,
    /// Stage 7.1 — `_submit` reaching the venue.
    Submit,
    /// Stage 7.1 — the venue receipt checks after `_submit`.
    Receipt,
    /// Stage 8 — building and appending the journal.
    Journal,
}

impl Gate {
    /// The pipeline state whose processing this gate belongs to.
    pub const fn stage(self) -> Stage {
        match self {
            Self::Idempotency | Self::Consent | Self::Sizing => Stage::Intent,
            Self::Risk | Self::KillBeforeVenue => Stage::RiskDecision,
            Self::Quote | Self::KillAfterQuote | Self::ReRisk => Stage::Quote,
            Self::OrderSize
            | Self::PositionRebound
            | Self::Sign
            | Self::PreSubmitKill
            | Self::BroadcastClaim => Stage::Order,
            Self::Submit | Self::Receipt => Stage::Fill,
            Self::Journal => Stage::Journal,
        }
    }

    /// The Python stage label, for the recorded table and for refusal traces.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Idempotency => "run/idempotency",
            Self::Consent => "0/consent",
            Self::Sizing => "1/sizing",
            Self::Risk => "2/risk",
            Self::KillBeforeVenue => "3/kill_before_venue",
            Self::Quote => "4/quote",
            Self::KillAfterQuote => "4.5/kill_after_quote",
            Self::ReRisk => "5/re_risk",
            Self::OrderSize => "6/order",
            Self::PositionRebound => "6.5/position_rebound",
            Self::Sign => "6.7/sign",
            Self::PreSubmitKill => "6.8/pre_submit_kill",
            Self::BroadcastClaim => "7/broadcast_claim",
            Self::Submit => "7.1/submit",
            Self::Receipt => "7.1/receipt",
            Self::Journal => "8/journal",
        }
    }
}

/// One edge of the machine: either a state was produced, or a gate refused.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Transition {
    /// The typed record for `stage` was produced.
    Enter { stage: Stage },
    /// The gate refused with a typed reason; [`Gate::stage`] is where it stopped.
    Refuse { gate: Gate, code: RefusalCode },
}

/// How a run ended.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Terminal {
    /// The key already held this payload: the cached result is replayed with
    /// `duplicate=True`; no new stage runs and nothing is inserted into the cache.
    Replay,
    /// The receipt was journaled; `partial` mirrors `fill.is_partial`.
    Journaled { partial: bool },
    /// A gate refused before the journal.
    Refused { gate: Gate, code: RefusalCode },
}

/// The opaque venue receipt a successful submit hands to the state machine. Its fields
/// are measured in Python; [`drive`] only passes them to [`receipt::verify_receipt`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReceiptVerdict {
    pub status: FillStatus,
    pub quantity: BoundAmount,
    pub notional_usd: BoundAmount,
    pub price_usd: BoundAmount,
    pub order_quantity: BoundAmount,
    /// The digest the receipt echoes from the signed payload.
    pub receipt_digest: Option<String>,
}

/// Every per-stage verdict the pure machine needs. Each field is produced by the Python
/// decimal/I/O/signing boundary; no value is computed here.
#[derive(Debug, Clone)]
pub struct RunInputs {
    pub key: String,
    pub side: Side,
    pub reduce_only: bool,
    /// SHA-256 intent fingerprint computed in Python (`_intent_fingerprint`).
    pub fingerprint: PayloadFingerprint,
    /// Fingerprint already bound to this key by the store or an in-flight claim, if any.
    pub prior_fingerprint: Option<PayloadFingerprint>,

    // Stage 0 — consent (opaque: Python resolves the AutomationSetting).
    pub consent: Result<(), RefusalCode>,
    // Stage 1 — sizing (opaque: Python divides a Decimal notional by a mark).
    pub sizing: Result<(), RefusalCode>,

    // Stage 2 / stage 5 — risk verdicts from the Python risk layer.
    pub pre_quote_risk: RiskVerdict,
    pub post_quote_risk: RiskVerdict,

    // Stage 4 — venue quote: presence/executability and route/size verdicts.
    pub quote: Quote,

    // Stage 6.5 — ledger-derived position rebound (opaque: Python replays the ledger).
    pub position_rebound_ok: bool,

    // Stage 6.7 — signing path and the opaque sign outcome (no keys are used here).
    pub signing: SigningPath,
    pub sign_verdict: Result<(), RefusalCode>,

    // Stage 7 — outbox claim (opaque durable write in Python).
    pub claim_ok: bool,

    // Stage 7.1 — submit outcome and, on success, the opaque receipt.
    pub submit: Result<ReceiptVerdict, RefusalCode>,
    /// The signed payload's digest (None for unsigned paper: no digest to echo).
    pub signed_payload_digest: Option<String>,

    // Stage 8 — journal build/append (opaque money arithmetic and sink I/O in Python).
    /// The journal group id on success (`fill:<order id>`), else the typed refusal.
    pub journal: Result<String, RefusalCode>,
}

fn receipt(
    status: FillStatus,
    quantity: &str,
    notional_usd: &str,
    price_usd: &str,
    order_quantity: &str,
) -> ReceiptVerdict {
    let parse = |raw: &str| BoundAmount::parse(raw).expect("test receipt amounts are valid");
    ReceiptVerdict {
        status,
        quantity: parse(quantity),
        notional_usd: parse(notional_usd),
        price_usd: parse(price_usd),
        order_quantity: parse(order_quantity),
        receipt_digest: Some("payload-digest".to_owned()),
    }
}

impl RunInputs {
    /// The canonical signed buy: a fresh key, every gate green, a filled receipt.
    /// Mirrors `tests/executor/test_pipeline.py::buy()` with `FakeVenue(mode="fill")`.
    pub fn buying() -> Self {
        Self {
            key: "intent-1".to_owned(),
            side: Side::Buy,
            reduce_only: false,
            fingerprint: PayloadFingerprint::new("fp-buy"),
            prior_fingerprint: None,
            consent: Ok(()),
            sizing: Ok(()),
            pre_quote_risk: RiskVerdict::Allowed,
            post_quote_risk: RiskVerdict::Allowed,
            quote: Quote::executable(),
            position_rebound_ok: true,
            signing: SigningPath::Signed,
            sign_verdict: Ok(()),
            claim_ok: true,
            submit: Ok(receipt(FillStatus::Filled, "50", "100", "2", "50")),
            signed_payload_digest: Some("payload-digest".to_owned()),
            journal: Ok("fill:order:intent-1".to_owned()),
        }
    }

    /// The canonical signed reduce-only exit: a sell that never reads the switch.
    /// Mirrors `tests/executor/test_pipeline.py::sell()`.
    pub fn exiting() -> Self {
        Self {
            key: "exit-1".to_owned(),
            side: Side::Sell,
            reduce_only: true,
            fingerprint: PayloadFingerprint::new("fp-sell"),
            prior_fingerprint: None,
            consent: Ok(()),
            sizing: Ok(()),
            pre_quote_risk: RiskVerdict::Allowed,
            post_quote_risk: RiskVerdict::Allowed,
            quote: Quote::executable(),
            position_rebound_ok: true,
            signing: SigningPath::Signed,
            sign_verdict: Ok(()),
            claim_ok: true,
            submit: Ok(receipt(FillStatus::Filled, "10", "20", "2", "10")),
            signed_payload_digest: Some("payload-digest".to_owned()),
            journal: Ok("fill:order:exit-1".to_owned()),
        }
    }
}

/// The complete typed trace of one intent through the machine.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Run {
    pub key: String,
    pub idempotency: IdempotencyDecision,
    /// The states actually produced, in order (a prefix of [`Stage::SEQUENCE`]).
    pub path: Vec<Stage>,
    pub transitions: Vec<Transition>,
    /// The gate-tree event trace in `gates::StageEvent` vocabulary. For the core gates it
    /// is byte-for-byte the trace `gates::evaluate` records for the same inputs.
    pub events: Vec<StageEvent>,
    pub terminal: Terminal,
    /// `gates::should_cache` of this run: true only when a result carried an order.
    pub cacheable: bool,
}

impl Run {
    /// The produced states as a slice.
    pub fn stages(&self) -> &[Stage] {
        &self.path
    }

    /// The typed refusal code, if the run stopped at a gate.
    pub fn refused_code(&self) -> Option<RefusalCode> {
        match self.terminal {
            Terminal::Refused { code, .. } => Some(code),
            _ => None,
        }
    }

    /// How many fresh kill-switch reads this run made (four, three or zero).
    pub fn kill_read_count(&self) -> usize {
        self.events
            .iter()
            .filter(|event| **event == StageEvent::KillSwitchRead)
            .count()
    }
}

/// One fresh entry-only kill-switch read, delegating to the existing kernel so there is
/// no second copy of the armed/unreadable semantics.
fn kill_gate<E>(side: Side, read: &mut impl FnMut() -> Result<bool, E>) -> Result<(), RefusalCode> {
    kill_switch::entry_gate(side, read).map_err(RefusalCode::from)
}

#[derive(Debug)]
struct Runner {
    key: String,
    idempotency: IdempotencyDecision,
    path: Vec<Stage>,
    transitions: Vec<Transition>,
    events: Vec<StageEvent>,
    terminal: Option<Terminal>,
}

impl Runner {
    fn new(inputs: &RunInputs, idempotency: IdempotencyDecision) -> Self {
        Self {
            key: inputs.key.clone(),
            idempotency,
            path: Vec::new(),
            transitions: Vec::new(),
            events: Vec::new(),
            terminal: None,
        }
    }

    fn enter(&mut self, stage: Stage) {
        self.path.push(stage);
        self.transitions.push(Transition::Enter { stage });
    }

    fn refuse(mut self, gate: Gate, code: RefusalCode) -> Run {
        self.transitions.push(Transition::Refuse { gate, code });
        self.terminal = Some(Terminal::Refused { gate, code });
        // Python sets `order` on the result only for submit failures, receipt failures and
        // journal results (`_order_failed` / the journal branch). `_refused_before_submit`
        // — including RECONCILIATION_FAILED — leaves it unset. Only an order-carrying
        // result is cached (`_should_cache`).
        let cacheable = matches!(gate, Gate::Submit | Gate::Receipt | Gate::Journal);
        self.finish(cacheable)
    }

    fn finish(self, cacheable: bool) -> Run {
        Run {
            key: self.key,
            idempotency: self.idempotency,
            path: self.path,
            transitions: self.transitions,
            events: self.events,
            terminal: self.terminal.expect("the machine always sets a terminal"),
            cacheable,
        }
    }

    /// An entry-only switch read, recorded in the shared event vocabulary.
    fn entry_read<E>(
        &mut self,
        side: Side,
        read: &mut impl FnMut() -> Result<bool, E>,
    ) -> Result<(), RefusalCode> {
        self.events.push(StageEvent::KillSwitchRead);
        kill_gate(side, read)
    }
}

/// Drive one intent through the machine in Python's exact order, calling the existing
/// kernels at each gate. `kill_read` is invoked once per entry gate: four times for a
/// signed buy, three for an unsigned paper buy, zero for any sell. Refusals never raise —
/// they are a terminal [`Run`], matching "`ExecutionPipeline.run` never raises for a
/// refused order".
pub fn drive<E>(inputs: &RunInputs, mut kill_read: impl FnMut() -> Result<bool, E>) -> Run {
    let idempotency = gates::classify(inputs.prior_fingerprint.as_ref(), &inputs.fingerprint);
    let mut runner = Runner::new(inputs, idempotency);

    // run: a key bound to a different payload is a conflict, never a replay; a key bound
    // to the same payload replays the cached result with duplicate=True.
    if idempotency == IdempotencyDecision::Conflict {
        return runner.refuse(Gate::Idempotency, RefusalCode::IdempotencyConflict);
    }
    if idempotency == IdempotencyDecision::Duplicate {
        runner.terminal = Some(Terminal::Replay);
        return runner.finish(false);
    }

    // Stage 0 — consent, before anything is sized or priced.
    if let Err(code) = inputs.consent {
        return runner.refuse(Gate::Consent, code);
    }
    // Stage 1 — size the request; risk sees the quantity the order will trade.
    if let Err(code) = inputs.sizing {
        return runner.refuse(Gate::Sizing, code);
    }
    runner.enter(Stage::Intent);

    // Stage 2 — risk before any venue contact or switch read.
    if let Err(code) = gates::risk_gate(inputs.pre_quote_risk) {
        return runner.refuse(Gate::Risk, code);
    }
    runner.enter(Stage::RiskDecision);

    // Stage 3 — kill switch before the venue (entry only).
    if inputs.side == Side::Buy {
        if let Err(code) = runner.entry_read(inputs.side, &mut kill_read) {
            return runner.refuse(Gate::KillBeforeVenue, code);
        }
    }

    // Stage 4 — quote.
    runner.events.push(StageEvent::Quote);
    if let Err(code) = gates::quote_stage_gate(inputs.quote) {
        return runner.refuse(Gate::Quote, code);
    }
    runner.enter(Stage::Quote);

    // Stage 4.5 — kill switch after the quote round-trip (entry only).
    if inputs.side == Side::Buy {
        if let Err(code) = runner.entry_read(inputs.side, &mut kill_read) {
            return runner.refuse(Gate::KillAfterQuote, code);
        }
    }

    // Stage 5 — re-risk against the quote (freshness refuses both sides).
    if let Err(code) = gates::risk_gate(inputs.post_quote_risk) {
        return runner.refuse(Gate::ReRisk, code);
    }

    // Stage 6 — order priced from the quote only when it is the approved size.
    if let Err(code) = gates::order_gate(inputs.quote, inputs.side, inputs.reduce_only) {
        return runner.refuse(Gate::OrderSize, code);
    }
    runner.enter(Stage::Order);

    // Stage 6.5 — a sell is re-bounded against the ledger-held quantity before submit.
    if inputs.side == Side::Sell && !inputs.position_rebound_ok {
        return runner.refuse(Gate::PositionRebound, RefusalCode::PositionChanged);
    }

    // Stage 6.7 — build, the sign-time kill read immediately before the key, and the
    // opaque signing outcome. An unsigned paper run skips all of this.
    if inputs.signing == SigningPath::Signed {
        runner.events.push(StageEvent::Prepare);
        if inputs.side == Side::Buy {
            if let Err(code) = runner.entry_read(inputs.side, &mut kill_read) {
                return runner.refuse(Gate::Sign, code);
            }
        }
        runner.events.push(StageEvent::Sign);
        if let Err(code) = inputs.sign_verdict {
            return runner.refuse(Gate::Sign, code);
        }
    }

    // Stage 6.8 — the last kill read, with nothing awaiting before submit.
    if inputs.side == Side::Buy {
        if let Err(code) = runner.entry_read(inputs.side, &mut kill_read) {
            return runner.refuse(Gate::PreSubmitKill, code);
        }
    }

    // Stage 7 — claim the broadcast durably before the venue sees the order.
    if !inputs.claim_ok {
        return runner.refuse(Gate::BroadcastClaim, RefusalCode::ReconciliationFailed);
    }

    // Stage 7.1 — submit, then verify the receipt against the order and signed digest.
    // The venue is contacted whether it accepts, times out or rejects, so the Submit
    // event is recorded before the outcome is examined.
    runner.events.push(StageEvent::Submit);
    let sub = match &inputs.submit {
        Err(code) => return runner.refuse(Gate::Submit, *code),
        Ok(sub) => sub,
    };
    if let Some(code) = receipt::verify_receipt(
        inputs.signed_payload_digest.as_deref(),
        sub.receipt_digest.as_deref(),
        sub.status,
        &sub.quantity,
        &sub.notional_usd,
        &sub.price_usd,
        &sub.order_quantity,
    ) {
        let code = RefusalCode::from_code(code).expect("the receipt kernel returns a known code");
        return runner.refuse(Gate::Receipt, code);
    }
    runner.enter(Stage::Fill);

    // Stage 8 — build and append the journal from the receipt. The ledger write itself
    // is Python's sink; here only the typed build/append verdict is observed.
    match &inputs.journal {
        Err(code) => runner.refuse(Gate::Journal, *code),
        Ok(_group_id) => {
            let partial = sub.status == FillStatus::Partial;
            runner.enter(Stage::Journal);
            runner.terminal = Some(Terminal::Journaled { partial });
            runner.finish(true)
        }
    }
}
