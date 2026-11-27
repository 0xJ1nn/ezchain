//! Pure LEDGER SEAL construction and verification order.
//!
//! This is the seal half of `packages/core-py/src/ezchain_core/ledger.py`, ported
//! verbatim in behaviour: [`canonical_timestamp`], [`chain_payload`], [`chain`],
//! [`ledger_digest`], [`seal_entries`] and [`verify_seal`]. The Python implementation
//! remains the oracle and the accountant (ADR-0049/0050 rule 4, `CONTRACT.md` I1);
//! this crate only reproduces the deterministic hashing order over entries that
//! Python already constructed and validated.
//!
//! # Boundary with Python
//!
//! Money arithmetic and ledger-entry construction stay in Python. Amounts cross
//! into this crate as **canonical text lexemes** — the exponent-free, fixed-point
//! output of Python's `_canonical_decimal` (for example `"1.5"`, `"-12.5"`, `"0"`).
//! Timestamps cross as the **canonical UTC spelling** `%Y-%m-%dT%H:%M:%S.%fZ`
//! produced by Python's `_canonical_timestamp`. Every other field is a plain string
//! or `None` (carried as `Option<String>`). Rust never parses, rounds, converts a
//! timezone or otherwise reinterprets a value: it commits to the exact lexemes it
//! was handed, just as the Python payload does.
//!
//! # Digest contract
//!
//! The canonical JSON is produced by `ezchain-jsoncanon`, which emits exactly the
//! bytes of `json.dumps(value, sort_keys=True, separators=(",", ":"),
//! ensure_ascii=True)`. The digest is `ezchain-fingerprint`'s SHA-256 over those
//! bytes, byte-identical to `hashlib.sha256(...).hexdigest()`. No new crypto
//! dependency is introduced (ADR-0052 assigns crypto to Rust, reusing the vetted
//! crate), and there is no I/O, key material, clock or ledger write here.
//!
//! # Verification order
//!
//! [`verify_seal`] checks the **count first at the seal boundary**, then recomputes
//! and compares the **per-entry chain** head. This is the reported-code order of
//! Python's `verify_seal`, whose `LedgerSeal` dataclass compares `count` before
//! `head`: a wrong count is reported as [`VerifyError::CountMismatch`] before any
//! head difference, and a same-count changed tape is reported as
//! [`VerifyError::HeadMismatch`]. Nothing is partially applied; a refusal leaves
//! the caller's tape untouched (fail closed, `CONTRACT.md` I3).

#![forbid(unsafe_code)]

use ezchain_fingerprint::fingerprint;
use ezchain_jsoncanon::{canonical_json, canonical_object, JsonValue};

/// Schema tag folded into every link payload (`LEDGER_DIGEST_SCHEMA` in Python).
pub const LEDGER_DIGEST_SCHEMA: &str = "ezchain.ledger/v2";

/// Digest of "no predecessor" (`LEDGER_GENESIS_DIGEST = "0" * 64` in Python).
pub const LEDGER_GENESIS_DIGEST: &str =
    "0000000000000000000000000000000000000000000000000000000000000000";

/// One immutable journal leg, carrying the exact canonical lexemes the seal commits
/// to. This mirrors Python's `LedgerEntry` projection without any `Decimal` or
/// arithmetic: `amount` and `usd_value` are canonical decimal text, `created_at` is
/// the canonical UTC spelling, and absent optional fields are `None`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LedgerEntry {
    pub account: String,
    pub asset: String,
    /// Canonical decimal lexeme (Python `_canonical_decimal` output), e.g. `"1.5"`.
    pub amount: String,
    /// Canonical decimal lexeme of the USD value.
    pub usd_value: String,
    pub kind: String,
    pub group_id: Option<String>,
    /// Canonical UTC spelling `%Y-%m-%dT%H:%M:%S.%fZ`, or `None`.
    pub created_at: Option<String>,
    pub wallet_id: Option<String>,
    pub bucket_id: Option<String>,
    pub actor_kind: Option<String>,
    pub actor_id: Option<String>,
}

/// The tamper-evidence seal of an ordered tape: its length and chained head.
/// Mirrors Python `LedgerSeal(count, head)`. Fields are private so a recorded seal
/// is immutable; construct one with [`LedgerSeal::new`] or [`seal_entries`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LedgerSeal {
    count: usize,
    head: String,
}

impl LedgerSeal {
    /// Record a seal from a count and a 64-hex head. The Python oracle is the source
    /// of these values; no validation happens here because a recorded seal is trusted
    /// input to [`verify_seal`], which reports a mismatch rather than guessing.
    #[must_use]
    pub fn new(count: usize, head: impl Into<String>) -> Self {
        LedgerSeal {
            count,
            head: head.into(),
        }
    }

    #[must_use]
    pub fn count(&self) -> usize {
        self.count
    }

    #[must_use]
    pub fn head(&self) -> &str {
        &self.head
    }
}

/// A typed refusal from [`verify_seal`]. `code()` is the stable token the parity
/// harness records for a case; it is the Rust counterpart of Python's single
/// `LedgerTamperedError`, refined by which field the dataclass comparison reaches
/// first.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum VerifyError {
    /// The tape length does not match the recorded seal's count (checked first).
    CountMismatch { expected: usize, found: usize },
    /// The tape has the recorded count but its chained head differs.
    HeadMismatch { expected: String, found: String },
}

impl VerifyError {
    #[must_use]
    pub fn code(&self) -> &'static str {
        match self {
            Self::CountMismatch { .. } => "CountMismatch",
            Self::HeadMismatch { .. } => "HeadMismatch",
        }
    }
}

impl std::fmt::Display for VerifyError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::CountMismatch { expected, found } => {
                write!(
                    f,
                    "ledger seal mismatch: expected {expected} entries, found {found}"
                )
            }
            Self::HeadMismatch { expected, found } => {
                write!(
                    f,
                    "ledger seal mismatch: expected head {expected}, found {found}"
                )
            }
        }
    }
}

impl std::error::Error for VerifyError {}

/// A spelling that is not the fixed-width UTC form produced by Python's
/// `_canonical_timestamp`. Returned by [`canonical_timestamp`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct InvalidTimestamp;

impl std::fmt::Display for InvalidTimestamp {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("not a canonical UTC timestamp (%Y-%m-%dT%H:%M:%S.%fZ)")
    }
}

impl std::error::Error for InvalidTimestamp {}

/// Validate the canonical UTC timestamp spelling `%Y-%m-%dT%H:%M:%S.%fZ`, the exact
/// output of Python's `_canonical_timestamp` (fixed width, six fractional digits,
/// `Z` suffix). Returns the spelling unchanged on success; a non-canonical spelling
/// is refused so it can never enter a digest (fail closed, `CONTRACT.md` I3).
///
/// The datetime-to-UTC *conversion* stays in Python, where entry construction lives;
/// this ports the **spelling** the seal commits to.
pub fn canonical_timestamp(spelling: &str) -> Result<&str, InvalidTimestamp> {
    let bytes = spelling.as_bytes();
    // "YYYY-MM-DDTHH:MM:SS.ffffffZ" is exactly 27 ASCII bytes.
    if bytes.len() != 27
        || bytes[4] != b'-'
        || bytes[7] != b'-'
        || bytes[10] != b'T'
        || bytes[13] != b':'
        || bytes[16] != b':'
        || bytes[19] != b'.'
        || bytes[26] != b'Z'
    {
        return Err(InvalidTimestamp);
    }
    for index in [
        0, 1, 2, 3, 5, 6, 8, 9, 11, 12, 14, 15, 17, 18, 20, 21, 22, 23, 24, 25,
    ] {
        if !bytes[index].is_ascii_digit() {
            return Err(InvalidTimestamp);
        }
    }
    Ok(spelling)
}

fn optional(value: &Option<String>) -> JsonValue {
    match value {
        Some(text) => JsonValue::Str(text.clone()),
        None => JsonValue::Null,
    }
}

/// The exact, unambiguous object one link commits to. Mirrors Python
/// `_chain_payload`: every field is a JSON scalar, and `None` is explicit rather
/// than omitted, so an entry that loses an optional field is a different entry.
fn chain_payload_value(index: usize, entry: &LedgerEntry) -> JsonValue {
    JsonValue::Object(vec![
        (
            "schema".to_string(),
            JsonValue::Str(LEDGER_DIGEST_SCHEMA.to_string()),
        ),
        ("index".to_string(), JsonValue::Int(index.to_string())),
        ("account".to_string(), JsonValue::Str(entry.account.clone())),
        ("asset".to_string(), JsonValue::Str(entry.asset.clone())),
        ("amount".to_string(), JsonValue::Str(entry.amount.clone())),
        (
            "usd_value".to_string(),
            JsonValue::Str(entry.usd_value.clone()),
        ),
        ("kind".to_string(), JsonValue::Str(entry.kind.clone())),
        ("group_id".to_string(), optional(&entry.group_id)),
        ("created_at".to_string(), optional(&entry.created_at)),
        ("wallet_id".to_string(), optional(&entry.wallet_id)),
        ("bucket_id".to_string(), optional(&entry.bucket_id)),
        ("actor_kind".to_string(), optional(&entry.actor_kind)),
        ("actor_id".to_string(), optional(&entry.actor_id)),
    ])
}

/// Canonical JSON bytes (as an ASCII `String`) of one entry's chain payload. This is
/// the inner object Python hands to `json.dumps` inside `_chain`; exposed so the
/// parity harness can assert byte-level equality, not just digest equality.
#[must_use]
pub fn chain_payload(index: usize, entry: &LedgerEntry) -> String {
    canonical_json(&chain_payload_value(index, entry))
        .expect("chain payload contains only Str/Int/Null scalars; canonical JSON cannot fail")
}

/// One SHA-256 link: the head, the one-based index and the canonical JSON payload.
/// Mirrors Python `_chain`: `{"previous": head, "entry": payload}` is serialized with
/// sorted keys and no whitespace, then hashed to lowercase hex.
#[must_use]
pub fn chain(head: &str, index: usize, entry: &LedgerEntry) -> String {
    let object = canonical_object([
        ("previous", JsonValue::Str(head.to_string())),
        ("entry", chain_payload_value(index, entry)),
    ])
    .expect("chain frame contains only a string and an object; canonical JSON cannot fail");
    fingerprint(object.as_bytes())
}

/// Chained SHA-256 head over an ordered tape starting from `previous`.
/// Mirrors Python `ledger_digest(entries, previous=...)`; indices start at one.
pub fn ledger_digest<'a, I>(entries: I, previous: &str) -> String
where
    I: IntoIterator<Item = &'a LedgerEntry>,
{
    let mut head = previous.to_string();
    for (offset, entry) in entries.into_iter().enumerate() {
        head = chain(&head, offset + 1, entry);
    }
    head
}

/// Chained SHA-256 head from [`LEDGER_GENESIS_DIGEST`]. Convenience for callers that
/// do not resume an existing tape.
pub fn digest_entries<'a, I>(entries: I) -> String
where
    I: IntoIterator<Item = &'a LedgerEntry>,
{
    ledger_digest(entries, LEDGER_GENESIS_DIGEST)
}

/// Seal an ordered tape: count every entry and fold the chain from genesis. The same
/// tape always produces the same seal (Python `seal_entries`).
pub fn seal_entries<'a, I>(entries: I) -> LedgerSeal
where
    I: IntoIterator<Item = &'a LedgerEntry>,
{
    let mut head = LEDGER_GENESIS_DIGEST.to_string();
    let mut count = 0usize;
    for (offset, entry) in entries.into_iter().enumerate() {
        count = offset + 1;
        head = chain(&head, count, entry);
    }
    LedgerSeal { count, head }
}

/// Recompute a tape's seal and compare it with the recorded one. The **count is
/// checked first at the seal boundary**, then the per-entry chain head is
/// recomputed and compared — the reported-code order of Python `verify_seal`
/// (whose `LedgerSeal` dataclass compares `count` before `head`). A refusal leaves
/// the caller's tape untouched.
pub fn verify_seal<'a, I>(entries: I, seal: &LedgerSeal) -> Result<(), VerifyError>
where
    I: IntoIterator<Item = &'a LedgerEntry>,
{
    let entries: Vec<&LedgerEntry> = entries.into_iter().collect();
    if entries.len() != seal.count() {
        return Err(VerifyError::CountMismatch {
            expected: seal.count(),
            found: entries.len(),
        });
    }
    let head = ledger_digest(entries.iter().copied(), LEDGER_GENESIS_DIGEST);
    if head != seal.head() {
        return Err(VerifyError::HeadMismatch {
            expected: seal.head().to_string(),
            found: head,
        });
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn entry(account: &str, amount: &str) -> LedgerEntry {
        LedgerEntry {
            account: account.to_string(),
            asset: "USD".to_string(),
            amount: amount.to_string(),
            usd_value: amount.to_string(),
            kind: "genesis".to_string(),
            group_id: None,
            created_at: None,
            wallet_id: None,
            bucket_id: None,
            actor_kind: None,
            actor_id: None,
        }
    }

    #[test]
    fn genesis_and_genesis_constants_are_stable() {
        assert_eq!(LEDGER_DIGEST_SCHEMA, "ezchain.ledger/v2");
        assert_eq!(LEDGER_GENESIS_DIGEST, "0".repeat(64));
    }

    #[test]
    fn canonical_timestamp_accepts_only_the_fixed_utc_spelling() {
        assert_eq!(
            canonical_timestamp("2026-11-18T10:30:00.123456Z"),
            Ok("2026-11-18T10:30:00.123456Z")
        );
        assert_eq!(
            canonical_timestamp("2026-11-18T10:30:00.000000Z"),
            Ok("2026-11-18T10:30:00.000000Z")
        );
        for bad in [
            "2026-11-18T10:30:00.123456+00:00",
            "2026-11-18T10:30:00Z",
            "2026-11-18T10:30:00.12345Z",
            "2026-11-18T10:30:00.123456z",
            "2026-11-18 10:30:00.123456Z",
            "",
        ] {
            assert_eq!(canonical_timestamp(bad), Err(InvalidTimestamp), "{bad:?}");
        }
    }

    #[test]
    fn empty_tape_seals_to_genesis_and_verifies() {
        let empty: Vec<LedgerEntry> = Vec::new();
        let seal = seal_entries(&empty);
        assert_eq!(seal.count(), 0);
        assert_eq!(seal.head(), LEDGER_GENESIS_DIGEST);
        verify_seal(&empty, &seal).expect("empty tape verifies");
        assert_eq!(digest_entries(&empty), LEDGER_GENESIS_DIGEST);
    }

    #[test]
    fn same_tape_is_deterministic_and_a_changed_entry_changes_the_head() {
        let tape = [
            entry("cash:USD", "10000"),
            entry("equity:genesis", "-10000"),
        ];
        let a = seal_entries(&tape);
        let b = seal_entries(&tape);
        assert_eq!(a, b);
        let mut changed = tape.clone();
        changed[1].amount = "-9999".to_string();
        assert_ne!(a.head(), seal_entries(&changed).head());
    }

    #[test]
    fn index_is_one_based_and_payload_has_explicit_nulls() {
        let payload = chain_payload(1, &entry("cash:USD", "1.5"));
        assert!(payload.contains("\"index\":1"));
        assert!(payload.contains("\"group_id\":null"));
        assert!(payload.contains("\"amount\":\"1.5\""));
        assert!(payload.contains("\"schema\":\"ezchain.ledger/v2\""));
        assert_ne!(payload, chain_payload(2, &entry("cash:USD", "1.5")));
    }

    #[test]
    fn verify_reports_count_before_head_and_is_fail_closed() {
        let tape = [
            entry("cash:USD", "10000"),
            entry("equity:genesis", "-10000"),
        ];
        let seal = seal_entries(&tape);
        verify_seal(&tape, &seal).expect("clean tape verifies");

        // Wrong count AND wrong head: count wins at the seal boundary.
        let wrong_both = LedgerSeal::new(99, "f".repeat(64));
        assert_eq!(
            verify_seal(&tape, &wrong_both).unwrap_err().code(),
            "CountMismatch"
        );

        // Right count, wrong head: the per-entry chain is reported.
        let wrong_head = LedgerSeal::new(seal.count(), "f".repeat(64));
        assert_eq!(
            verify_seal(&tape, &wrong_head).unwrap_err().code(),
            "HeadMismatch"
        );

        // A truncated prefix verifies its own chain clean and is caught by count.
        assert_eq!(
            verify_seal(&tape[..1], &seal).unwrap_err().code(),
            "CountMismatch"
        );

        // A same-count corrupted entry is a head mismatch.
        let mut corrupted = tape.clone();
        corrupted[1].amount = "-9999".to_string();
        assert_eq!(
            verify_seal(&corrupted, &seal).unwrap_err().code(),
            "HeadMismatch"
        );
    }

    #[test]
    fn ledger_digest_accepts_a_non_genesis_predecessor() {
        let tape = [entry("cash:USD", "1")];
        let previous = "a".repeat(64);
        let from_genesis = digest_entries(&tape);
        let from_custom = ledger_digest(&tape, &previous);
        assert_ne!(from_genesis, from_custom);
        assert_eq!(ledger_digest(&tape, &previous), from_custom);
    }
}
