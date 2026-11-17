//! Per-source-class freshness classification and the one-alert-per-transition tracker,
//! ported from `ezchain_core.freshness`.
//!
//! The Python original is the behavioural oracle. Every branch of `FreshnessPolicy.classify`
//! and `SourceFreshness.observe` is recorded in the tests below, including the exact budget
//! boundary, the clock-fault case, the failure cases of policy construction, and the bounded
//! alert queue. `stale_reason` is not copied here: this module calls the already-ported
//! [`crate::freshness::stale_reason`], so the reason-code split has one home.
//!
//! Two boundary representations differ from Python by necessity and are kept honest:
//!
//! * Time is a caller-defined `f64` number of seconds. Python subtracts `datetime`s to get
//!   `age_seconds`/`future_skew_seconds`; Rust receives the same two instants as `f64` and
//!   performs the identical arithmetic. Callers inject `now` — there is no clock I/O.
//! * `SourceRef` carries the ISO string of the observation alongside its numeric instant.
//!   Python's `observed_at` is a `datetime`, which holds both; Rust's boundary supplies both
//!   explicitly and the string is passed through to the alert verbatim.

#![forbid(unsafe_code)]

use std::collections::{BTreeMap, VecDeque};
use std::fmt;

use crate::freshness::stale_reason;

/// The default age budget for a source whose class is not in the override map.
pub const DEFAULT_MAX_AGE_SECONDS: f64 = 60.0;

/// How far an observation may run ahead of the reader's clock before it is a clock fault.
/// One constant for the writer, the reader and the risk gate (`MARK_FUTURE_SKEW_SECONDS`).
pub const MARK_FUTURE_SKEW_SECONDS: f64 = 5.0;

/// An alert queue nobody drains must not grow without bound.
pub const MAX_PENDING_ALERTS: usize = 256;

const FUTURE_REASON: &str = "MARK_TIMESTAMP_FUTURE";
const PROVIDER_STALE: &str = "provider_stale";

/// Per-class defaults, in seconds. A class is a kind of read, not a venue: two mark sources
/// can have different budgets without the code naming either of them.
pub fn default_max_age_by_class() -> BTreeMap<String, f64> {
    BTreeMap::from([
        ("mark".to_string(), 60.0),
        ("quote".to_string(), 60.0),
        ("candle".to_string(), 300.0),
        ("feed".to_string(), 60.0),
    ])
}

/// The four-value freshness vocabulary (`MarkStatus` in the Python original).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MarkStatus {
    Fresh,
    Stale,
    Unknown,
    Future,
}

impl MarkStatus {
    /// The stable token the Python original stores in `FreshnessVerdict.status`.
    pub fn as_str(self) -> &'static str {
        match self {
            MarkStatus::Fresh => "fresh",
            MarkStatus::Stale => "stale",
            MarkStatus::Unknown => "unknown",
            MarkStatus::Future => "future",
        }
    }
}

/// One observation: a source identity, its numeric instant and the ISO label at the boundary.
#[derive(Debug, Clone, PartialEq)]
pub struct SourceRef {
    pub name: String,
    pub url: Option<String>,
    /// Observation instant on the same clock as `now`, in seconds.
    pub observed_at: f64,
    /// Python's `SourceRef.observed_at.isoformat()`, carried through to the alert.
    pub observed_at_iso: String,
}

impl SourceRef {
    /// Seconds this observation runs ahead of `now`; negative when it is past.
    /// Mirrors `SourceRef.future_skew_seconds`.
    pub fn future_skew_seconds(&self, now: f64) -> f64 {
        self.observed_at - now
    }

    /// Age of the observation, never negative. Mirrors `SourceRef.age_seconds`.
    pub fn age_seconds(&self, now: f64) -> f64 {
        (now - self.observed_at).max(0.0)
    }
}

/// The server's own freshness verdict for one observation.
#[derive(Debug, Clone, PartialEq)]
pub struct FreshnessVerdict {
    pub status: MarkStatus,
    pub age_seconds: Option<f64>,
    pub reason: Option<&'static str>,
    pub source_class: String,
}

impl FreshnessVerdict {
    /// Whether the observation may price anything. Only `fresh` is usable.
    pub fn usable(&self) -> bool {
        self.status == MarkStatus::Fresh
    }
}

/// A refused policy construction. `code` mirrors the stable reason the API returns.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FreshnessError {
    pub code: &'static str,
    pub message: String,
}

impl fmt::Display for FreshnessError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for FreshnessError {}

/// Per-source-class age budgets and the read gate that applies them.
#[derive(Debug, Clone, PartialEq)]
pub struct FreshnessPolicy {
    pub max_age_by_class: BTreeMap<String, f64>,
    pub default_max_age_seconds: f64,
    pub max_future_skew_seconds: f64,
}

impl Default for FreshnessPolicy {
    fn default() -> Self {
        Self::new()
    }
}

impl FreshnessPolicy {
    /// The Python `FreshnessPolicy()` defaults. The defaults are always valid.
    pub fn new() -> Self {
        Self {
            max_age_by_class: default_max_age_by_class(),
            default_max_age_seconds: DEFAULT_MAX_AGE_SECONDS,
            max_future_skew_seconds: MARK_FUTURE_SKEW_SECONDS,
        }
    }

    /// The Python keyword constructor, including its `__post_init__` validation.
    pub fn with_options(
        max_age_by_class: BTreeMap<String, f64>,
        default_max_age_seconds: f64,
        max_future_skew_seconds: f64,
    ) -> Result<Self, FreshnessError> {
        if default_max_age_seconds <= 0.0 {
            return Err(FreshnessError {
                code: "POLICY_INVALID",
                message: "default_max_age_seconds must be positive".to_string(),
            });
        }
        for (source_class, budget) in &max_age_by_class {
            if *budget <= 0.0 {
                return Err(FreshnessError {
                    code: "POLICY_INVALID",
                    message: format!("max age for '{source_class}' must be positive"),
                });
            }
        }
        Ok(Self {
            max_age_by_class,
            default_max_age_seconds,
            max_future_skew_seconds,
        })
    }

    /// The Python `FreshnessPolicy(default_max_age_seconds=...)` case.
    pub fn with_default_max_age(default_max_age_seconds: f64) -> Result<Self, FreshnessError> {
        let policy = Self::new();
        Self::with_options(
            policy.max_age_by_class,
            default_max_age_seconds,
            policy.max_future_skew_seconds,
        )
    }

    /// The Python `FreshnessPolicy(max_age_by_class=...)` case.
    pub fn with_class_budgets(
        max_age_by_class: BTreeMap<String, f64>,
    ) -> Result<Self, FreshnessError> {
        let policy = Self::new();
        Self::with_options(
            max_age_by_class,
            policy.default_max_age_seconds,
            policy.max_future_skew_seconds,
        )
    }

    /// Budget for `source_class`: its override, or the default when unlisted.
    pub fn budget(&self, source_class: &str) -> f64 {
        self.max_age_by_class
            .get(source_class)
            .copied()
            .unwrap_or(self.default_max_age_seconds)
    }

    /// Classify one observation. Missing, stale and far-future are never fresh.
    ///
    /// One vocabulary per `MarkStatus`: a past-budget age is `stale` and names the class's
    /// stale reason (`SNAPSHOT_STALE`/`QUOTE_STALE`); a timestamp beyond the tolerated skew
    /// is the contract's clock fault and takes `unknown` with `MARK_TIMESTAMP_FUTURE`, never a
    /// stale reason. A modest future is `future`, also not fresh.
    pub fn classify(
        &self,
        source: Option<&SourceRef>,
        source_class: &str,
        now: f64,
    ) -> FreshnessVerdict {
        let verdict = |status, age, reason| FreshnessVerdict {
            status,
            age_seconds: age,
            reason,
            source_class: source_class.to_string(),
        };

        let Some(source) = source else {
            return verdict(MarkStatus::Unknown, None, None);
        };
        let skew = source.future_skew_seconds(now);
        if skew > self.max_future_skew_seconds {
            return verdict(MarkStatus::Unknown, None, Some(FUTURE_REASON));
        }
        if skew > 0.0 {
            return verdict(MarkStatus::Future, None, None);
        }
        let age = source.age_seconds(now);
        if age > self.budget(source_class) {
            return verdict(
                MarkStatus::Stale,
                Some(age),
                Some(stale_reason(source_class)),
            );
        }
        verdict(MarkStatus::Fresh, Some(age), None)
    }

    /// The price only when it is positive and its source is fresh; otherwise `None`.
    ///
    /// Generic so no decimal type or arithmetic is introduced: the port checks only
    /// positivity and freshness, exactly as the Python original does.
    pub fn price<T: PartialOrd + From<u8>>(
        &self,
        price: Option<T>,
        source: Option<&SourceRef>,
        source_class: &str,
        now: f64,
    ) -> Option<T> {
        let price = price?;
        if price <= T::from(0u8) {
            return None;
        }
        if !self.classify(source, source_class, now).usable() {
            return None;
        }
        Some(price)
    }
}

/// `alert.raised` for a source that just went stale. One per transition.
#[derive(Debug, Clone, PartialEq)]
pub struct ProviderStaleAlert {
    pub entity: String,
    pub source_class: String,
    pub severity: &'static str,
    pub type_code: &'static str,
    pub dedupe_key: String,
    pub age_seconds: Option<f64>,
    pub observed_at: Option<String>,
    pub message: String,
}

impl ProviderStaleAlert {
    /// Python's `ProviderStaleAlert(entity=..., ...)` with its declared defaults.
    pub fn new(
        entity: String,
        source_class: String,
        dedupe_key: String,
        age_seconds: Option<f64>,
        observed_at: Option<String>,
        message: String,
    ) -> Self {
        Self {
            entity,
            source_class,
            severity: "warning",
            type_code: PROVIDER_STALE,
            dedupe_key,
            age_seconds,
            observed_at,
            message,
        }
    }

    /// The fields of Python's `to_dict()`; `type_code` is the `"type"` key (`type` is a
    /// Rust keyword). All eight fields are present with the same values.
    pub fn to_fields(&self) -> AlertFields {
        AlertFields {
            severity: self.severity,
            type_code: self.type_code,
            entity: self.entity.clone(),
            source_class: self.source_class.clone(),
            dedupe_key: self.dedupe_key.clone(),
            age_seconds: self.age_seconds,
            observed_at: self.observed_at.clone(),
            message: self.message.clone(),
        }
    }
}

/// The eight key/value pairs of Python's `ProviderStaleAlert.to_dict()`.
#[derive(Debug, Clone, PartialEq)]
pub struct AlertFields {
    pub severity: &'static str,
    /// Python key `"type"`.
    pub type_code: &'static str,
    pub entity: String,
    pub source_class: String,
    pub dedupe_key: String,
    pub age_seconds: Option<f64>,
    pub observed_at: Option<String>,
    pub message: String,
}

/// Track one set of sources and raise `provider_stale` once per fresh→stale transition.
///
/// `observe` is called on every read. An unknown or future-dated observation counts as
/// stale: a source we cannot date is a source we cannot trust.
#[derive(Debug, Clone)]
pub struct SourceFreshness {
    policy: FreshnessPolicy,
    stale: BTreeMap<String, bool>,
    alerts: VecDeque<ProviderStaleAlert>,
    capacity: usize,
}

impl Default for SourceFreshness {
    fn default() -> Self {
        Self::new()
    }
}

impl SourceFreshness {
    /// Python `SourceFreshness()`.
    pub fn new() -> Self {
        Self::with_policy(FreshnessPolicy::new(), MAX_PENDING_ALERTS)
    }

    /// Python `SourceFreshness(policy=..., max_pending=...)`; the queue is at least one long.
    pub fn with_policy(policy: FreshnessPolicy, max_pending: usize) -> Self {
        Self {
            policy,
            stale: BTreeMap::new(),
            alerts: VecDeque::new(),
            capacity: max_pending.max(1),
        }
    }

    /// Observe one source. Returns an alert only on the fresh→stale edge.
    pub fn observe(
        &mut self,
        entity: &str,
        source: Option<&SourceRef>,
        source_class: &str,
        now: f64,
    ) -> Option<ProviderStaleAlert> {
        let verdict = self.policy.classify(source, source_class, now);
        let stale = !verdict.usable();
        let was_stale = self.stale.get(entity).copied().unwrap_or(false);
        self.stale.insert(entity.to_string(), stale);
        if !stale || was_stale {
            return None;
        }

        let budget = self.policy.budget(source_class);
        let message =
            if verdict.reason == Some(FUTURE_REASON) || verdict.status == MarkStatus::Future {
                format!(
                "{entity} is dated ahead of the clock; {source_class} read refused (budget {}s).",
                format_g(budget)
            )
            } else if verdict.status == MarkStatus::Unknown {
                format!(
                    "{entity} has no observation; {source_class} budget {}s.",
                    format_g(budget)
                )
            } else {
                let age = verdict.age_seconds.unwrap_or(0.0);
                format!(
                    "{entity} is stale: age {}s exceeds the {source_class} budget {}s.",
                    format_g(age),
                    format_g(budget)
                )
            };

        let alert = ProviderStaleAlert::new(
            entity.to_string(),
            source_class.to_string(),
            format!("{entity}:{PROVIDER_STALE}"),
            verdict.age_seconds,
            source.map(|source| source.observed_at_iso.clone()),
            message,
        );
        if self.alerts.len() == self.capacity {
            self.alerts.pop_front();
        }
        self.alerts.push_back(alert.clone());
        Some(alert)
    }

    /// Whether `entity` is currently stale. Unknown entities are not stale.
    pub fn is_stale(&self, entity: &str) -> bool {
        self.stale.get(entity).copied().unwrap_or(false)
    }

    /// A snapshot of every pending alert, oldest first.
    pub fn alerts(&self) -> Vec<ProviderStaleAlert> {
        self.alerts.iter().cloned().collect()
    }

    /// Return every alert raised so far and forget them, for a publisher to deliver.
    pub fn drain(&mut self) -> Vec<ProviderStaleAlert> {
        self.alerts.drain(..).collect()
    }
}

/// Python's `:g` general format (default precision six), used in alert messages.
///
/// Rust's `format!` has no `g` specifier, so this reproduces the two regimes: fixed notation
/// for exponents in `[-4, 6)`, scientific otherwise, with trailing zeros stripped and the
/// exponent signed and at least two digits wide.
fn format_g(value: f64) -> String {
    if value == 0.0 {
        return if value.is_sign_negative() {
            "-0".to_string()
        } else {
            "0".to_string()
        };
    }
    let negative = value < 0.0;
    let magnitude = value.abs();
    let exponent = magnitude.log10().floor() as i32;
    let mut body = if (-4..6).contains(&exponent) {
        let decimals = (5 - exponent).max(0) as usize;
        trim_zeros(&format!("{magnitude:.decimals$}"))
    } else {
        let mantissa = trim_zeros(&format!("{:.5}", magnitude / 10f64.powi(exponent)));
        let sign = if exponent < 0 { '-' } else { '+' };
        format!("{mantissa}e{sign}{:02}", exponent.abs())
    };
    if negative {
        body.insert(0, '-');
    }
    body
}

fn trim_zeros(text: &str) -> String {
    if !text.contains('.') {
        return text.to_string();
    }
    text.trim_end_matches('0').trim_end_matches('.').to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    // NOW is 0 on the injected clock; a source `seconds_ago` old sits at `-seconds_ago`.
    const NOW: f64 = 0.0;
    const PRICE: i64 = 2000;

    fn source(seconds_ago: f64) -> SourceRef {
        SourceRef {
            name: "dexscreener".to_string(),
            url: Some("https://dexscreener.com".to_string()),
            observed_at: NOW - seconds_ago,
            observed_at_iso: "2026-01-01T12:00:00+00:00".to_string(),
        }
    }

    fn class_budgets(pairs: &[(&str, f64)]) -> BTreeMap<String, f64> {
        pairs
            .iter()
            .map(|(key, value)| ((*key).to_string(), *value))
            .collect()
    }

    // ---- FreshnessPolicy.classify: every branch of the original ------------------------

    #[test]
    fn an_observation_inside_the_budget_is_fresh() {
        let verdict = FreshnessPolicy::new().classify(Some(&source(59.5)), "mark", NOW);
        assert_eq!(verdict.status, MarkStatus::Fresh);
        assert!(verdict.usable());
        assert_eq!(verdict.reason, None);
        assert_eq!(verdict.age_seconds, Some(59.5));
        assert_eq!(verdict.source_class, "mark");
    }

    #[test]
    fn an_observation_past_the_budget_is_stale_and_prices_nothing() {
        let policy = FreshnessPolicy::new();
        let verdict = policy.classify(Some(&source(60.5)), "mark", NOW);
        assert_eq!(verdict.status, MarkStatus::Stale);
        assert!(!verdict.usable());
        assert_eq!(verdict.reason, Some("SNAPSHOT_STALE"));
        assert_eq!(verdict.age_seconds, Some(60.5));
        assert_eq!(
            policy.price(Some(PRICE), Some(&source(60.5)), "mark", NOW),
            None
        );
    }

    #[test]
    fn a_fresh_observation_prices() {
        assert_eq!(
            FreshnessPolicy::new().price(Some(PRICE), Some(&source(1.0)), "mark", NOW),
            Some(PRICE)
        );
    }

    #[test]
    fn a_far_future_observation_is_unknown_with_the_clock_fault_reason() {
        let verdict = FreshnessPolicy::new().classify(Some(&source(-30.0)), "mark", NOW);
        assert_eq!(verdict.status, MarkStatus::Unknown);
        assert!(!verdict.usable());
        assert_eq!(verdict.age_seconds, None);
        assert_eq!(verdict.reason, Some("MARK_TIMESTAMP_FUTURE"));
    }

    #[test]
    fn a_modest_future_observation_is_labelled_future_not_stale() {
        let verdict = FreshnessPolicy::new().classify(Some(&source(-1.0)), "mark", NOW);
        assert_eq!(verdict.status, MarkStatus::Future);
        assert_eq!(verdict.reason, None);
        assert!(!verdict.usable());
        assert_eq!(verdict.age_seconds, None);
    }

    #[test]
    fn no_observation_is_unknown_not_fresh() {
        let verdict = FreshnessPolicy::new().classify(None, "mark", NOW);
        assert_eq!(verdict.status, MarkStatus::Unknown);
        assert!(!verdict.usable());
        assert_eq!(verdict.age_seconds, None);
        assert_eq!(verdict.reason, None);
    }

    #[test]
    fn a_missing_or_non_positive_price_never_prices() {
        let policy = FreshnessPolicy::new();
        for price in [None, Some(0), Some(-1)] {
            assert_eq!(policy.price(price, Some(&source(0.0)), "mark", NOW), None);
        }
    }

    #[test]
    fn budgets_are_per_source_class() {
        let policy = FreshnessPolicy::with_class_budgets(class_budgets(&[
            ("mark", 30.0),
            ("candle", 600.0),
        ]))
        .unwrap();
        let aged = source(120.0);
        assert_eq!(
            policy.classify(Some(&aged), "mark", NOW).status,
            MarkStatus::Stale
        );
        assert_eq!(
            policy.classify(Some(&aged), "candle", NOW).status,
            MarkStatus::Fresh
        );
        assert_eq!(policy.budget("mark"), 30.0);
        assert_eq!(policy.budget("candle"), 600.0);
    }

    #[test]
    fn an_unlisted_class_falls_back_to_the_default_budget() {
        let policy = FreshnessPolicy::with_options(
            class_budgets(&[("mark", 30.0)]),
            90.0,
            MARK_FUTURE_SKEW_SECONDS,
        )
        .unwrap();
        assert_eq!(policy.budget("feed"), 90.0);
        assert_eq!(policy.budget("mark"), 30.0);
    }

    #[test]
    fn quote_and_mark_refusals_name_different_reason_codes() {
        let policy = FreshnessPolicy::new();
        let aged = source(90.0);
        assert_eq!(
            policy.classify(Some(&aged), "quote", NOW).reason,
            Some("QUOTE_STALE")
        );
        assert_eq!(
            policy.classify(Some(&aged), "mark", NOW).reason,
            Some("SNAPSHOT_STALE")
        );
    }

    #[test]
    fn the_defaults_are_bounded_and_never_zero() {
        let defaults = default_max_age_by_class();
        const { assert!(DEFAULT_MAX_AGE_SECONDS > 0.0) };
        assert!(defaults.values().all(|budget| *budget > 0.0));
        assert_eq!(
            FreshnessPolicy::new().budget("mark"),
            DEFAULT_MAX_AGE_SECONDS
        );
    }

    #[test]
    fn a_non_positive_budget_is_rejected() {
        for bad in [0.0, -1.0] {
            let class_error =
                FreshnessPolicy::with_class_budgets(class_budgets(&[("mark", bad)])).unwrap_err();
            assert_eq!(class_error.message, "max age for 'mark' must be positive");
            let default_error = FreshnessPolicy::with_default_max_age(bad).unwrap_err();
            assert_eq!(
                default_error.message,
                "default_max_age_seconds must be positive"
            );
        }
    }

    #[test]
    fn the_budget_boundary_is_exact() {
        // 60.0 is still inside the budget; only past it is stale (the pinned edge).
        for (seconds_ago, status) in [
            (60.0, MarkStatus::Fresh),
            (59.999, MarkStatus::Fresh),
            (60.001, MarkStatus::Stale),
            (61.0, MarkStatus::Stale),
        ] {
            let verdict = FreshnessPolicy::new().classify(Some(&source(seconds_ago)), "mark", NOW);
            assert_eq!(verdict.status, status, "age {seconds_ago}");
            assert_eq!(verdict.usable(), status == MarkStatus::Fresh);
        }
    }

    // ---- SourceFreshness: the transition tracker and its alert queue -------------------

    #[test]
    fn one_alert_per_transition_not_one_per_read() {
        let mut tracker = SourceFreshness::new();
        assert!(tracker
            .observe("mark:blast", Some(&source(0.0)), "mark", NOW)
            .is_none());
        let first = tracker
            .observe("mark:blast", Some(&source(61.0)), "mark", NOW)
            .unwrap();
        assert_eq!(first.type_code, "provider_stale");
        assert_eq!(first.entity, "mark:blast");
        assert_eq!(first.dedupe_key, "mark:blast:provider_stale");
        assert_eq!(first.age_seconds, Some(61.0));
        assert!(tracker
            .observe("mark:blast", Some(&source(62.0)), "mark", NOW)
            .is_none());
        assert!(tracker
            .observe("mark:blast", Some(&source(120.0)), "mark", NOW)
            .is_none());
        assert_eq!(tracker.alerts().len(), 1);
    }

    #[test]
    fn recovery_closes_the_outage_so_a_later_one_raises_again() {
        let mut tracker = SourceFreshness::new();
        assert!(tracker
            .observe("mark:blast", Some(&source(0.0)), "mark", NOW)
            .is_none());
        assert!(tracker
            .observe("mark:blast", Some(&source(61.0)), "mark", NOW)
            .is_some());
        assert!(tracker.is_stale("mark:blast"));
        assert!(tracker
            .observe("mark:blast", Some(&source(1.0)), "mark", NOW)
            .is_none());
        assert!(!tracker.is_stale("mark:blast"));
        assert!(tracker
            .observe("mark:blast", Some(&source(90.0)), "mark", NOW)
            .is_some());
        assert_eq!(tracker.alerts().len(), 2);
    }

    #[test]
    fn a_source_never_observed_alerts_as_stale() {
        let mut tracker = SourceFreshness::new();
        let alert = tracker.observe("feed:pumpfun", None, "feed", NOW).unwrap();
        assert_eq!(alert.age_seconds, None);
        assert_eq!(alert.observed_at, None);
        assert!(alert.message.contains("no observation"));
        assert_eq!(
            alert.message,
            "feed:pumpfun has no observation; feed budget 60s."
        );
    }

    #[test]
    fn a_future_dated_source_alerts_and_says_why() {
        let mut tracker = SourceFreshness::new();
        let alert = tracker
            .observe("mark:blast", Some(&source(-30.0)), "mark", NOW)
            .unwrap();
        assert!(alert.message.contains("ahead of the clock"));
        assert_eq!(
            alert.message,
            "mark:blast is dated ahead of the clock; mark read refused (budget 60s)."
        );
        assert_eq!(alert.age_seconds, None);
    }

    #[test]
    fn a_stale_source_alert_names_its_age_and_budget() {
        let mut tracker = SourceFreshness::new();
        let alert = tracker
            .observe("mark:blast", Some(&source(61.0)), "mark", NOW)
            .unwrap();
        assert_eq!(
            alert.message,
            "mark:blast is stale: age 61s exceeds the mark budget 60s."
        );
        assert_eq!(alert.severity, "warning");
        assert_eq!(alert.source_class, "mark");
        assert_eq!(alert.observed_at, Some(source(61.0).observed_at_iso));
    }

    #[test]
    fn a_modest_future_source_alerts_with_the_clock_fault_message() {
        let mut tracker = SourceFreshness::new();
        let alert = tracker
            .observe("mark:blast", Some(&source(-1.0)), "mark", NOW)
            .unwrap();
        assert_eq!(
            alert.message,
            "mark:blast is dated ahead of the clock; mark read refused (budget 60s)."
        );
    }

    #[test]
    fn drain_hands_the_alerts_over_once() {
        let mut tracker = SourceFreshness::new();
        tracker.observe("mark:blast", Some(&source(61.0)), "mark", NOW);
        let drained = tracker.drain();
        assert_eq!(drained.len(), 1);
        assert!(tracker.drain().is_empty());
        assert_eq!(drained[0].to_fields().type_code, "provider_stale");
        let fields = drained[0].to_fields();
        assert_eq!(fields.severity, "warning");
        assert_eq!(fields.entity, "mark:blast");
        assert_eq!(fields.source_class, "mark");
        assert_eq!(fields.dedupe_key, "mark:blast:provider_stale");
        assert_eq!(fields.age_seconds, Some(61.0));
    }

    #[test]
    fn two_sources_track_independently() {
        let mut tracker = SourceFreshness::new();
        tracker.observe("mark:blast", Some(&source(61.0)), "mark", NOW);
        assert!(tracker
            .observe("mark:base", Some(&source(0.0)), "mark", NOW)
            .is_none());
        assert!(tracker.is_stale("mark:blast"));
        assert!(!tracker.is_stale("mark:base"));
        assert!(!tracker.is_stale("never:observed"));
    }

    #[test]
    fn entities_are_keyed_by_chain_token_and_kind() {
        let mut tracker = SourceFreshness::new();
        assert!(tracker
            .observe("mark:blast:0xa", Some(&source(61.0)), "mark", NOW)
            .is_some());
        assert!(tracker
            .observe("mark:blast:0xb", Some(&source(61.0)), "mark", NOW)
            .is_some());
        assert!(tracker
            .observe("mark:base:0xa", Some(&source(61.0)), "mark", NOW)
            .is_some());
        assert_eq!(tracker.alerts().len(), 3);
    }

    #[test]
    fn the_pending_alert_queue_is_bounded() {
        let mut tracker = SourceFreshness::with_policy(FreshnessPolicy::new(), 2);
        for token in ["0xa", "0xb", "0xc"] {
            assert!(tracker
                .observe(
                    &format!("mark:blast:{token}"),
                    Some(&source(61.0)),
                    "mark",
                    NOW
                )
                .is_some());
        }
        let drained = tracker.drain();
        assert_eq!(drained.len(), 2);
        assert_eq!(
            drained
                .iter()
                .map(|alert| alert.entity.as_str())
                .collect::<Vec<_>>(),
            ["mark:blast:0xb", "mark:blast:0xc"]
        );
    }

    #[test]
    fn a_zero_pending_limit_still_keeps_one_alert() {
        let mut tracker = SourceFreshness::with_policy(FreshnessPolicy::new(), 0);
        tracker.observe("mark:blast", Some(&source(61.0)), "mark", NOW);
        assert_eq!(tracker.alerts().len(), 1);
    }

    // ---- The :g formatter used by the pinned message strings ----------------------------

    #[test]
    fn general_format_matches_python_g_for_the_emitted_magnitudes() {
        assert_eq!(format_g(60.0), "60");
        assert_eq!(format_g(61.0), "61");
        assert_eq!(format_g(600.0), "600");
        assert_eq!(format_g(0.5), "0.5");
        assert_eq!(format_g(60.001), "60.001");
        assert_eq!(format_g(0.0), "0");
        assert_eq!(format_g(1e-7), "1e-07");
        assert_eq!(format_g(1e6), "1e+06");
    }
}
