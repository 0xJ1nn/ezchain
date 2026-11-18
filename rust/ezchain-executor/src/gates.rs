//! Gate order and refusal semantics, ported from `ezchain_executor.pipeline` and the
//! pure risk/quote value objects it consumes (`ezchain_core`).
//!
//! This module carries the *decision tree* of the reference pipeline, not its money
//! arithmetic, I/O, signing or ledger writes. Python remains the oracle: every code and
//! every branch here is recorded from `pipeline.py`, `execution.py` and `risk.py` and is
//! covered by the parity tests in `tests/gates_parity.rs`.
//!
//! What is ported, in the exact order `ExecutionPipeline._run` applies it:
//!
//! 1. The pre-quote risk decision (stage 2). An unknown/unreachable verdict refuses with
//!    `MISSING_RISK_INPUTS` — unknown is never zero and never "allowed" (CONTRACT I3).
//! 2. The kill switch read before venue contact (stage 3), after the quote round-trip
//!    (stage 4.5), immediately before the key (stage 6.7, inside `_sign`) and immediately
//!    before submit (stage 6.8). Every read is *entry only*: a sell — reduce-only or
//!    ordinary — is bounded by the held position and never gated, so an arm cannot trap
//!    an exit. An unreadable switch refuses an entry and is never treated as "off".
//! 3. The quote gates: a missing/malformed/timed-out quote produces no order, and a
//!    stale quote refuses both sides at the re-risk stage. The size/route checks at the
//!    order stage refuse a quote that differs from the risk-approved size.
//! 4. The idempotency rule: the same key with a different payload fingerprint is a
//!    `IDEMPOTENCY_CONFLICT`, the same key with the same fingerprint is a duplicate, and
//!    only order-producing results are cached (a pre-submit denial is retryable).
//!
//! Money arithmetic (Decimal sizing, freshness computation, digest computation, journal
//! construction) stays in Python. Intent, risk and quote are modelled as opaque typed
//! records: the boolean flags and verdicts they carry are *produced by* the Python
//! decimal-owning layer and merely *compared* here. No arithmetic is reimplemented.

use crate::kill_switch::{self, KillSwitchRefusal};
use crate::Side;

/// A typed refusal code. Each spelling is the exact reason string the pipeline records
/// on `ExecutionResult.reason` for that branch.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RefusalCode {
    /// Risk service unreachable / verdict we cannot classify (`MISSING_RISK_INPUTS`).
    MissingRiskInputs,
    /// A risk denial whose primary reason is not one of the codes modelled here; the
    /// stable fallback Python uses when `decision.reasons` is empty (`RISK_DENIED`).
    RiskDenied,
    /// Market mark unknown on a buy (`PRICE_UNKNOWN`).
    PriceUnknown,
    /// Stale executable quote; refuses both sides at re-risk (`QUOTE_STALE`).
    QuoteStale,
    /// The venue quote timed out (`QUOTE_TIMEOUT`).
    QuoteTimeout,
    /// No quote, a malformed quote, or a quote that is not executable (`NO_QUOTE`).
    NoQuote,
    /// A buy quote with no positive route (`NO_ROUTE`).
    NoRoute,
    /// An ordinary sell quote with no positive route (`NO_SELL_ROUTE`).
    NoSellRoute,
    /// A reduce-only exit quote with no positive route (`EXIT_NO_ROUTE`).
    ExitNoRoute,
    /// The quote's size differs from the risk-approved size (`QUOTE_SIZE_MISMATCH`).
    QuoteSizeMismatch,
    /// Operator switch armed (`KILL_SWITCH_ACTIVE`).
    KillSwitchActive,
    /// Operator switch could not be read (`KILL_SWITCH_UNAVAILABLE`).
    KillSwitchUnavailable,
    /// A sell whose held position shrank during the quote round-trip (`POSITION_CHANGED`).
    PositionChanged,
    /// A reused idempotency key naming a different payload (`IDEMPOTENCY_CONFLICT`).
    IdempotencyConflict,
    /// An unattended intent with no matching automation consent (`AUTOMATION_CONSENT_REQUIRED`).
    AutomationConsentRequired,
    /// A sell with no held position (`INSUFFICIENT_POSITION`).
    InsufficientPosition,
    /// A sell that resolves to a non-positive size (`INVALID_AMOUNT`).
    InvalidAmount,
    /// A live/mode submit with no signing boundary (`SIGNING_BOUNDARY_REQUIRED`).
    SigningBoundaryRequired,
    /// A non-paper submit whose signer is not vault/custody backed (`VAULT_BOUNDARY_REQUIRED`).
    VaultBoundaryRequired,
    /// The key handle's owner differs from the order's principal (`CUSTODY_OWNER_MISMATCH`).
    CustodyOwnerMismatch,
    /// The approved intent cannot be constructed (`TRANSACTION_NOT_APPROVABLE`).
    TransactionNotApprovable,
    /// The venue could not build the transaction (`TRANSACTION_BUILD_FAILED`).
    TransactionBuildFailed,
    /// The signer failed (`TRANSACTION_SIGN_FAILED`).
    TransactionSignFailed,
    /// The broadcast claim could not be written (`RECONCILIATION_FAILED`).
    ReconciliationFailed,
    /// The venue submit timed out (`TIMEOUT`).
    Timeout,
    /// The venue rejected the order (`VENUE_REJECTED`).
    VenueRejected,
    /// The venue adapter raised an unmapped error (`VENUE_ERROR`).
    VenueError,
    /// A receipt that is not accepted/sized/priced (`UNBOOKABLE_RECEIPT`).
    UnbookableReceipt,
    /// A receipt larger than the approved order (`RECEIPT_EXCEEDS_ORDER`).
    ReceiptExceedsOrder,
    /// A receipt that does not echo the signed payload digest (`TRANSACTION_DIGEST_MISMATCH`).
    TransactionDigestMismatch,
    /// The journal could not be built or appended (`JOURNAL_FAILED`).
    JournalFailed,
}

impl RefusalCode {
    /// The stable Python reason string.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::MissingRiskInputs => "MISSING_RISK_INPUTS",
            Self::RiskDenied => "RISK_DENIED",
            Self::PriceUnknown => "PRICE_UNKNOWN",
            Self::QuoteStale => "QUOTE_STALE",
            Self::QuoteTimeout => "QUOTE_TIMEOUT",
            Self::NoQuote => "NO_QUOTE",
            Self::NoRoute => "NO_ROUTE",
            Self::NoSellRoute => "NO_SELL_ROUTE",
            Self::ExitNoRoute => "EXIT_NO_ROUTE",
            Self::QuoteSizeMismatch => "QUOTE_SIZE_MISMATCH",
            Self::KillSwitchActive => "KILL_SWITCH_ACTIVE",
            Self::KillSwitchUnavailable => "KILL_SWITCH_UNAVAILABLE",
            Self::PositionChanged => "POSITION_CHANGED",
            Self::IdempotencyConflict => "IDEMPOTENCY_CONFLICT",
            Self::AutomationConsentRequired => "AUTOMATION_CONSENT_REQUIRED",
            Self::InsufficientPosition => "INSUFFICIENT_POSITION",
            Self::InvalidAmount => "INVALID_AMOUNT",
            Self::SigningBoundaryRequired => "SIGNING_BOUNDARY_REQUIRED",
            Self::VaultBoundaryRequired => "VAULT_BOUNDARY_REQUIRED",
            Self::CustodyOwnerMismatch => "CUSTODY_OWNER_MISMATCH",
            Self::TransactionNotApprovable => "TRANSACTION_NOT_APPROVABLE",
            Self::TransactionBuildFailed => "TRANSACTION_BUILD_FAILED",
            Self::TransactionSignFailed => "TRANSACTION_SIGN_FAILED",
            Self::ReconciliationFailed => "RECONCILIATION_FAILED",
            Self::Timeout => "TIMEOUT",
            Self::VenueRejected => "VENUE_REJECTED",
            Self::VenueError => "VENUE_ERROR",
            Self::UnbookableReceipt => "UNBOOKABLE_RECEIPT",
            Self::ReceiptExceedsOrder => "RECEIPT_EXCEEDS_ORDER",
            Self::TransactionDigestMismatch => "TRANSACTION_DIGEST_MISMATCH",
            Self::JournalFailed => "JOURNAL_FAILED",
        }
    }

    /// Resolve a stable Python reason string to its typed code. Receipt-verification
    /// kernels (`crate::receipt`) return their refusals as the exact Python strings, so
    /// the state machine maps them back here rather than owning a second vocabulary.
    pub fn from_code(code: &str) -> Option<Self> {
        Some(match code {
            "MISSING_RISK_INPUTS" => Self::MissingRiskInputs,
            "RISK_DENIED" => Self::RiskDenied,
            "PRICE_UNKNOWN" => Self::PriceUnknown,
            "QUOTE_STALE" => Self::QuoteStale,
            "QUOTE_TIMEOUT" => Self::QuoteTimeout,
            "NO_QUOTE" => Self::NoQuote,
            "NO_ROUTE" => Self::NoRoute,
            "NO_SELL_ROUTE" => Self::NoSellRoute,
            "EXIT_NO_ROUTE" => Self::ExitNoRoute,
            "QUOTE_SIZE_MISMATCH" => Self::QuoteSizeMismatch,
            "KILL_SWITCH_ACTIVE" => Self::KillSwitchActive,
            "KILL_SWITCH_UNAVAILABLE" => Self::KillSwitchUnavailable,
            "POSITION_CHANGED" => Self::PositionChanged,
            "IDEMPOTENCY_CONFLICT" => Self::IdempotencyConflict,
            "AUTOMATION_CONSENT_REQUIRED" => Self::AutomationConsentRequired,
            "INSUFFICIENT_POSITION" => Self::InsufficientPosition,
            "INVALID_AMOUNT" => Self::InvalidAmount,
            "SIGNING_BOUNDARY_REQUIRED" => Self::SigningBoundaryRequired,
            "VAULT_BOUNDARY_REQUIRED" => Self::VaultBoundaryRequired,
            "CUSTODY_OWNER_MISMATCH" => Self::CustodyOwnerMismatch,
            "TRANSACTION_NOT_APPROVABLE" => Self::TransactionNotApprovable,
            "TRANSACTION_BUILD_FAILED" => Self::TransactionBuildFailed,
            "TRANSACTION_SIGN_FAILED" => Self::TransactionSignFailed,
            "RECONCILIATION_FAILED" => Self::ReconciliationFailed,
            "TIMEOUT" => Self::Timeout,
            "VENUE_REJECTED" => Self::VenueRejected,
            "VENUE_ERROR" => Self::VenueError,
            "UNBOOKABLE_RECEIPT" => Self::UnbookableReceipt,
            "RECEIPT_EXCEEDS_ORDER" => Self::ReceiptExceedsOrder,
            "TRANSACTION_DIGEST_MISMATCH" => Self::TransactionDigestMismatch,
            "JOURNAL_FAILED" => Self::JournalFailed,
            _ => return None,
        })
    }
}

impl From<KillSwitchRefusal> for RefusalCode {
    fn from(refusal: KillSwitchRefusal) -> Self {
        match refusal {
            KillSwitchRefusal::Active => Self::KillSwitchActive,
            KillSwitchRefusal::Unavailable => Self::KillSwitchUnavailable,
        }
    }
}

/// The risk verdict, modelled as an opaque record produced by the Python risk layer.
///
/// `Unknown` is a distinct state, never "allowed" and never zero. It is the typed form
/// of `risk_decision_from_intent` returning `MISSING_RISK_INPUTS` when it cannot evaluate
/// (no snapshot), and of CONTRACT I3's "unreachable risk service -> no new order".
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RiskVerdict {
    /// `RiskDecision(allowed=True, reasons=())`.
    Allowed,
    /// `RiskDecision(allowed=False, reasons=(code,))` — the primary reason.
    Denied(RefusalCode),
    /// No classifiable verdict was produced. Refuses with `MISSING_RISK_INPUTS`.
    Unknown,
}

/// Gate the risk decision (pipeline stages 2 and 5). An unknown verdict fails closed.
pub fn risk_gate(verdict: RiskVerdict) -> Result<(), RefusalCode> {
    match verdict {
        RiskVerdict::Allowed => Ok(()),
        RiskVerdict::Denied(code) => Err(code),
        RiskVerdict::Unknown => Err(RefusalCode::MissingRiskInputs),
    }
}

/// An opaque executable quote. Every field is a verdict computed in Python with Decimal
/// or timestamp arithmetic; this crate only branches on it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Quote {
    /// The venue returned a quote (no timeout / exception / missing value).
    pub present: bool,
    /// `_quote_is_executable`: price > 0, amount_in > 0, amount_out > 0, source present.
    pub executable: bool,
    /// A positive route exists for the quoted side (route/quantity checks).
    pub route_ok: bool,
    /// `_consistent_size`: the quote size equals the risk-approved size up to rounding.
    pub size_ok: bool,
    /// The quote request timed out at the venue (`QUOTE_TIMEOUT`).
    pub timed_out: bool,
}

impl Quote {
    /// A fully executable, fresh-size quote that passes every gate.
    pub const fn executable() -> Self {
        Self {
            present: true,
            executable: true,
            route_ok: true,
            size_ok: true,
            timed_out: false,
        }
    }
}

/// Gate the quote acquisition stage (`ExecutionPipeline._quote`, stage 4).
///
/// A timeout, a missing quote or a non-executable quote produces no order on either side.
pub fn quote_stage_gate(quote: Quote) -> Result<(), RefusalCode> {
    if quote.timed_out {
        return Err(RefusalCode::QuoteTimeout);
    }
    if !quote.present || !quote.executable {
        return Err(RefusalCode::NoQuote);
    }
    Ok(())
}

/// Gate the order construction stage (`_order_from_quote`, stage 6). The route reason is
/// named for the exit path on sells exactly as `evaluate_order` names it at re-risk.
pub fn order_gate(quote: Quote, side: Side, reduce_only: bool) -> Result<(), RefusalCode> {
    if !quote.route_ok {
        return Err(match side {
            Side::Buy => RefusalCode::NoRoute,
            Side::Sell if reduce_only => RefusalCode::ExitNoRoute,
            Side::Sell => RefusalCode::NoSellRoute,
        });
    }
    if !quote.size_ok {
        return Err(RefusalCode::QuoteSizeMismatch);
    }
    Ok(())
}

/// Whether this run builds, binds and signs a transaction before submit. Paper may run
/// unsigned (no sign-time kill-switch read); every other mode configures a boundary.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SigningPath {
    /// `mode == paper` and no signing boundary: `_sign` is skipped.
    UnsignedPaper,
    /// A signing boundary is configured: prepare, sign-time gate, sign.
    Signed,
}

/// One observable stage in the ordered gate trace. The names match the events the Python
/// acceptance test records in `test_one_buying_intent_crosses_the_switch_four_times`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StageEvent {
    /// A fresh read of the operator kill switch.
    KillSwitchRead,
    /// The venue quote round-trip.
    Quote,
    /// The transaction builder round-trip (before the sign-time read).
    Prepare,
    /// The signer call.
    Sign,
    /// The venue submit — reached only when no gate refused.
    Submit,
}

/// The result of walking the gate tree for one intent.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GateOutcome {
    /// Every gate passed and the intent reached the venue submit.
    Proceed { events: Vec<StageEvent> },
    /// A gate refused the intent before submit, with the ordered events up to that gate.
    Refused {
        code: RefusalCode,
        events: Vec<StageEvent>,
    },
}

/// All inputs the ordered decision tree needs. Money values are absent by design: the
/// flags carry the Python layer's verdicts, so no Decimal arithmetic is reimplemented.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct GateInputs {
    pub side: Side,
    pub reduce_only: bool,
    /// `risk_decision_from_intent` before a quote exists (stage 2).
    pub pre_quote_risk: RiskVerdict,
    /// `risk_decision_from_intent` re-run with the quote (stage 5). Staleness is carried
    /// here as `RiskVerdict::Denied(RefusalCode::QuoteStale)`, as it is in Python.
    pub post_quote_risk: RiskVerdict,
    pub quote: Quote,
    pub signing: SigningPath,
    /// For a sell, whether the held position still bounds the order at submit (stage 6.5,
    /// a ledger-derived check). `false` maps to `POSITION_CHANGED`.
    pub position_rebound_ok: bool,
}

impl GateInputs {
    /// The canonical buying intent: entry gates active, full signing, everything good.
    pub const fn buying() -> Self {
        Self {
            side: Side::Buy,
            reduce_only: false,
            pre_quote_risk: RiskVerdict::Allowed,
            post_quote_risk: RiskVerdict::Allowed,
            quote: Quote::executable(),
            signing: SigningPath::Signed,
            position_rebound_ok: true,
        }
    }

    /// The canonical reduce-only exit: sells are never gated by entry checks.
    pub const fn exiting() -> Self {
        Self {
            side: Side::Sell,
            reduce_only: true,
            pre_quote_risk: RiskVerdict::Allowed,
            post_quote_risk: RiskVerdict::Allowed,
            quote: Quote::executable(),
            signing: SigningPath::Signed,
            position_rebound_ok: true,
        }
    }
}

/// Drive one intent through the gate tree in the exact order `ExecutionPipeline._run`
/// applies the gates. `kill_read` is called once per entry gate — four times for a signed
/// buy, zero times for any sell — so an arm landing between stages is seen by the next
/// read, exactly as in Python.
pub fn evaluate<E>(
    inputs: &GateInputs,
    mut kill_read: impl FnMut() -> Result<bool, E>,
) -> GateOutcome {
    let mut events = Vec::new();
    let is_entry = inputs.side == Side::Buy;

    // Stage 2 — risk before any venue contact or switch read.
    if let Err(code) = risk_gate(inputs.pre_quote_risk) {
        return GateOutcome::Refused { code, events };
    }

    // Stage 3 — kill switch before the venue is contacted (entry only).
    if is_entry {
        events.push(StageEvent::KillSwitchRead);
        if let Err(code) = read_gate(&mut kill_read) {
            return GateOutcome::Refused { code, events };
        }
    }

    // Stage 4 — quote.
    events.push(StageEvent::Quote);
    if let Err(code) = quote_stage_gate(inputs.quote) {
        return GateOutcome::Refused { code, events };
    }

    // Stage 4.5 — kill switch after the quote round-trip (entry only).
    if is_entry {
        events.push(StageEvent::KillSwitchRead);
        if let Err(code) = read_gate(&mut kill_read) {
            return GateOutcome::Refused { code, events };
        }
    }

    // Stage 5 — re-risk against the quote (freshness refuses both sides).
    if let Err(code) = risk_gate(inputs.post_quote_risk) {
        return GateOutcome::Refused { code, events };
    }

    // Stage 6 — order priced from the quote only when it is the approved size.
    if let Err(code) = order_gate(inputs.quote, inputs.side, inputs.reduce_only) {
        return GateOutcome::Refused { code, events };
    }

    // Stage 6.5 — a sell is re-bounded against the ledger-held quantity before submit.
    if inputs.side == Side::Sell && !inputs.position_rebound_ok {
        return GateOutcome::Refused {
            code: RefusalCode::PositionChanged,
            events,
        };
    }

    // Stage 6.7 — build, then the sign-time kill-switch read immediately before the key.
    if inputs.signing == SigningPath::Signed {
        events.push(StageEvent::Prepare);
        if is_entry {
            events.push(StageEvent::KillSwitchRead);
            if let Err(code) = read_gate(&mut kill_read) {
                return GateOutcome::Refused { code, events };
            }
        }
        events.push(StageEvent::Sign);
    }

    // Stage 6.8 — the last kill-switch read, with nothing awaiting before submit.
    if is_entry {
        events.push(StageEvent::KillSwitchRead);
        if let Err(code) = read_gate(&mut kill_read) {
            return GateOutcome::Refused { code, events };
        }
    }

    // Stage 7 — the venue is contacted only after every gate passed.
    events.push(StageEvent::Submit);
    GateOutcome::Proceed { events }
}

/// One fresh kill-switch read, mapping to the typed refusal. Mirrors
/// [`kill_switch::check`] (and therefore `KillSwitch.check`) with no duplicated logic.
fn read_gate<E>(read: &mut impl FnMut() -> Result<bool, E>) -> Result<(), RefusalCode> {
    match kill_switch::check(read) {
        Ok(()) => Ok(()),
        Err(refusal) => Err(refusal.into()),
    }
}

// ---------------------------------------------------------------------------
// Idempotency: key-to-payload binding (pipeline.run / DedupeStore).
// ---------------------------------------------------------------------------

/// An opaque payload fingerprint. Python computes this as the SHA-256 of the canonical
/// intent fields (`_intent_fingerprint`); hashing needs a dependency this crate refuses,
/// so the digest travels in as an opaque token and is only compared here.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PayloadFingerprint(String);

impl PayloadFingerprint {
    pub fn new(token: impl Into<String>) -> Self {
        Self(token.into())
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// The decision for a key that is already known (stored result or in-flight claim).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IdempotencyDecision {
    /// Key is new: run the intent.
    Fresh,
    /// Same key, same fingerprint: replay the cached/claimed result with `duplicate=True`.
    Duplicate,
    /// Same key, different fingerprint: `IDEMPOTENCY_CONFLICT`, never a replay.
    Conflict,
}

/// The single rule used for both the idempotency store and the in-flight claim in
/// `ExecutionPipeline.run`: a key is bound to the payload fingerprint it first carried.
pub fn classify(
    existing: Option<&PayloadFingerprint>,
    incoming: &PayloadFingerprint,
) -> IdempotencyDecision {
    match existing {
        None => IdempotencyDecision::Fresh,
        Some(stored) if stored == incoming => IdempotencyDecision::Duplicate,
        Some(_) => IdempotencyDecision::Conflict,
    }
}

/// `_should_cache`: only results that produced an order may be cached. A pre-submit
/// refusal carries no order and is retryable, so a fixed snapshot or fresh quote can run.
pub const fn should_cache(has_order: bool) -> bool {
    has_order
}

/// A bounded, least-recently-used idempotency store — the pure data structure behind
/// `DedupeStore`. `get` promotes an entry, `insert` promotes and evicts the oldest entry
/// when the bound is exceeded. `max_entries` must be positive, as in Python.
#[derive(Debug, Clone)]
pub struct IdempotencyStore<V> {
    max_entries: usize,
    // Index 0 is the least recently used entry.
    entries: Vec<(String, PayloadFingerprint, V)>,
}

impl<V> IdempotencyStore<V> {
    pub fn new(max_entries: usize) -> Result<Self, &'static str> {
        if max_entries == 0 {
            return Err("the idempotency store needs at least one entry");
        }
        Ok(Self {
            max_entries,
            entries: Vec::new(),
        })
    }

    pub fn get(&mut self, key: &str) -> Option<(&PayloadFingerprint, &V)> {
        let index = self.entries.iter().position(|(k, _, _)| k == key)?;
        let entry = self.entries.remove(index);
        self.entries.push(entry);
        let (_, fingerprint, value) = self.entries.last().expect("just pushed");
        Some((fingerprint, value))
    }

    pub fn insert(&mut self, key: impl Into<String>, fingerprint: PayloadFingerprint, value: V) {
        let key = key.into();
        if let Some(index) = self.entries.iter().position(|(k, _, _)| k == &key) {
            self.entries.remove(index);
        }
        self.entries.push((key, fingerprint, value));
        while self.entries.len() > self.max_entries {
            self.entries.remove(0);
        }
    }

    pub fn contains(&self, key: &str) -> bool {
        self.entries.iter().any(|(k, _, _)| k == key)
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
}
