#![forbid(unsafe_code)]
//! Pure append-only storage and replay kernel.
//!
//! This module models the *storage discipline* `docs/CONTRACT.md` requires without opening a
//! database, file, socket or clock. The store assigns every sequence number; entries are immutable
//! once appended; writes only ever extend the tail; replay derives state from a whole ordered tape.
//! A deterministic FNV-1a digest chain makes a changed, dropped or reordered tape visible, and a
//! corrupt tape is quarantined rather than partially trusted (fail closed, CONTRACT I3). Resident
//! memory is bounded by an explicit eviction policy.
//!
//! **The digest chain is tamper-evidence only, NOT a signature.** It is unkeyed: anyone holding the
//! bytes can recompute it. Its job is to detect accidental corruption and divergence inside a
//! trusted operator group, exactly as ADR-0051 §"Message contract" states for the P2P content id.
//! It proves nothing about authorship, does not notarise time and is not a security boundary.
//!
//! **What stays outside this kernel.** There is no money arithmetic, no Decimal, no ledger write
//! path and no key material here (ADR-0050 rule 4, CONTRACT I1/I4). The Python `ledger.py` remains
//! the accountant and the oracle for ledger cases; Postgres remains the durable tape. This kernel
//! owns only the in-process working set: the caller keeps the full ordered tape it collects from
//! [`AppendLog::append`] (or loads from its durable store) and hands it to [`seal`],
//! [`verify_tape`] and [`replay`]. Eviction drops only the resident suffix, never the caller's tape.

use std::collections::VecDeque;

/// Schema tag folded into every digest. Length-prefixed fields keep one entry's bytes from running
/// into the next — the ambiguity the Python ledger fixed when it moved from `"|"` joins to
/// canonical JSON (`ledger.py`: `LEDGER_DIGEST_SCHEMA`, ADR v2).
const SCHEMA_TAG: &[u8] = b"ezchain.append-log/v1";

/// The digest of "no predecessor". A whole-tape seal of an empty tape is `{ count: 0, head:
/// GENESIS_DIGEST }`, mirroring Python's `LEDGER_GENESIS_DIGEST = "0" * 64`.
pub const GENESIS_DIGEST: u64 = 0;

/// FNV-1a over 64-bit words, the standard offset basis and prime. Deterministic, dependency-free
/// and deliberately non-cryptographic: a content id and tamper-evidence aid, never a signature.
pub fn fnv1a64(bytes: &[u8]) -> u64 {
    const OFFSET: u64 = 0xcbf2_9ce4_8422_2325;
    const PRIME: u64 = 0x0000_0100_0000_01b3;
    let mut hash = OFFSET;
    for byte in bytes {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(PRIME);
    }
    hash
}

fn digest_bytes(prev: u64, seq: u64, payload: &[u8]) -> Vec<u8> {
    let mut bytes = Vec::with_capacity(SCHEMA_TAG.len() + 24 + payload.len());
    bytes.extend_from_slice(SCHEMA_TAG);
    bytes.extend_from_slice(&prev.to_le_bytes());
    bytes.extend_from_slice(&seq.to_le_bytes());
    bytes.extend_from_slice(&(payload.len() as u64).to_le_bytes());
    bytes.extend_from_slice(payload);
    bytes
}

/// One link's content digest. The previous head, the store-assigned sequence and the length-
/// prefixed opaque payload are all committed to, so changing any of them changes the digest.
#[must_use]
pub fn entry_digest(prev: u64, seq: u64, payload: &[u8]) -> u64 {
    fnv1a64(&digest_bytes(prev, seq, payload))
}

/// One immutable journal entry. Fields are private: only the store builds entries, and readers get
/// a shared reference or an owned clone, never a mutable handle. `payload` is opaque bytes with no
/// monetary meaning and no arithmetic.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entry {
    seq: u64,
    prev: u64,
    digest: u64,
    payload: Vec<u8>,
}

impl Entry {
    /// Store-assigned, starting at one and never reused.
    #[must_use]
    pub fn seq(&self) -> u64 {
        self.seq
    }

    /// The digest of the preceding entry, or [`GENESIS_DIGEST`] for the first one.
    #[must_use]
    pub fn prev(&self) -> u64 {
        self.prev
    }

    /// This entry's own chain digest.
    #[must_use]
    pub fn digest(&self) -> u64 {
        self.digest
    }

    /// The opaque payload. Borrowed so a reader cannot substitute it.
    #[must_use]
    pub fn payload(&self) -> &[u8] {
        &self.payload
    }
}

/// The tamper-evidence seal of an ordered tape: its length and chained head. Mirrors Python
/// `LedgerSeal(count, head)`; verification compares count first, then head.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Seal {
    pub count: u64,
    pub head: u64,
}

/// What happens when the bounded resident window is full. Explicit, so a caller can choose between
/// a fail-closed store and a streaming tail without either being a silent default.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EvictionPolicy {
    /// Refuse the append with [`AppendError::AtCapacity`] and leave the store unchanged.
    RejectOnFull,
    /// Drop the oldest resident entry (FIFO) to make room for the new tail.
    DropOldest,
}

/// Bounds for one store. `capacity` must be positive; `max_payload_bytes` bounds each opaque
/// payload so a single huge append cannot defeat bounded memory.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Limits {
    pub max_payload_bytes: usize,
    pub capacity: usize,
    pub eviction: EvictionPolicy,
}

impl Limits {
    #[must_use]
    pub fn new(max_payload_bytes: usize, capacity: usize, eviction: EvictionPolicy) -> Self {
        assert!(
            capacity > 0,
            "a store must hold at least one resident entry"
        );
        Limits {
            max_payload_bytes,
            capacity,
            eviction,
        }
    }
}

/// A typed integrity failure of a tape. The first failing check wins and names its position, so
/// the reason is reproducible (CONTRACT I9: record the exact failing dimension).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TapeError {
    PayloadTooLarge {
        index: usize,
        len: usize,
        max: usize,
    },
    /// A sequence does not advance by exactly one: a gap, a drop or a reorder.
    SequenceGap {
        index: usize,
        expected: u64,
        found: u64,
    },
    /// The entry's own digest no longer matches its committed content.
    DigestMismatch {
        index: usize,
    },
    /// The entry does not name the digest that precedes it on the tape.
    ChainLinkMismatch {
        index: usize,
    },
    CountMismatch {
        expected: u64,
        found: u64,
    },
    HeadMismatch {
        expected: u64,
        found: u64,
    },
}

/// A refused store operation. Payload-size refusals leave the tape untouched; an integrity failure
/// during [`AppendLog::ingest`] quarantines the store and is returned as `Quarantined`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AppendError {
    PayloadTooLarge { len: usize, max: usize },
    AtCapacity { capacity: usize },
    SequenceExhausted,
    Sealed,
    Quarantined(TapeError),
}

/// The one declared lifecycle of a tape version. One writer — the store's own methods — moves it
/// along these edges. `Quarantined` and `Sealed` are terminal for writes; a new run is a new store.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LogState {
    /// Accepts appends.
    Open,
    /// Holds a verified seal; refuses further appends. Still replayable.
    Sealed(Seal),
    /// A corrupt tape was ingested or an operator intervened. Refuses appends and replays.
    Quarantined(TapeError),
}

/// Walk an ordered tape and return its computed seal. Checks, in this order, at every position:
/// sequence continuity (gap detection), the entry's own digest, then the link to its predecessor.
/// This is the structural half of Python `verify_events` plus the fold of `seal_entries`.
pub fn verify_chain(tape: &[Entry]) -> Result<Seal, TapeError> {
    let mut head = GENESIS_DIGEST;
    for (index, entry) in tape.iter().enumerate() {
        let expected = index as u64 + 1;
        if entry.seq != expected {
            return Err(TapeError::SequenceGap {
                index,
                expected,
                found: entry.seq,
            });
        }
        if entry_digest(entry.prev, entry.seq, &entry.payload) != entry.digest {
            return Err(TapeError::DigestMismatch { index });
        }
        if entry.prev != head {
            return Err(TapeError::ChainLinkMismatch { index });
        }
        head = entry.digest;
    }
    Ok(Seal {
        count: tape.len() as u64,
        head,
    })
}

/// Seal a clean tape. Refuses to seal a corrupt one, so a recorded seal never describes a tape
/// that failed verification. Same tape, same seal (Python `seal_entries`).
pub fn seal(tape: &[Entry]) -> Result<Seal, TapeError> {
    verify_chain(tape)
}

/// Recompute a tape's seal and compare it with the recorded one. The structural checks in
/// [`verify_chain`] run first (so a gap is reported before a seal mismatch); then count is compared
/// before head, mirroring Python `verify_seal`.
pub fn verify_tape(tape: &[Entry], expected: &Seal) -> Result<(), TapeError> {
    let computed = verify_chain(tape)?;
    if computed.count != expected.count {
        return Err(TapeError::CountMismatch {
            expected: expected.count,
            found: computed.count,
        });
    }
    if computed.head != expected.head {
        return Err(TapeError::HeadMismatch {
            expected: expected.head,
            found: computed.head,
        });
    }
    Ok(())
}

/// The derived, non-monetary projection of a whole ordered tape. Replay verifies before folding:
/// a corrupt tape is never partly applied (Python `replay` calls `verify_events` first).
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct DerivedState {
    /// Number of applied entries.
    pub count: u64,
    /// Applied sequence numbers in tape order.
    pub applied: Vec<u64>,
    /// Digest head after the last entry.
    pub head: u64,
    /// Total opaque bytes replayed. Byte count is not money arithmetic.
    pub payload_bytes: u64,
}

/// Replay a whole tape into a deterministic derived state.
pub fn replay(tape: &[Entry]) -> Result<DerivedState, TapeError> {
    verify_chain(tape)?;
    let mut state = DerivedState::default();
    for entry in tape {
        state.applied.push(entry.seq);
        state.payload_bytes += entry.payload.len() as u64;
        state.head = entry.digest;
        state.count += 1;
    }
    Ok(state)
}

/// A deterministic content id for a replayed state, so two replays can be compared without shipping
/// the state. FNV-1a over a length-prefixed canonical encoding; a comparison aid, not a signature.
#[must_use]
pub fn state_digest(state: &DerivedState) -> u64 {
    fn push_u64(bytes: &mut Vec<u8>, value: u64) {
        bytes.extend_from_slice(&value.to_le_bytes());
    }
    let mut bytes = Vec::new();
    push_u64(&mut bytes, SCHEMA_TAG.len() as u64);
    bytes.extend_from_slice(SCHEMA_TAG);
    push_u64(&mut bytes, state.count);
    push_u64(&mut bytes, state.applied.len() as u64);
    for seq in &state.applied {
        push_u64(&mut bytes, *seq);
    }
    push_u64(&mut bytes, state.head);
    push_u64(&mut bytes, state.payload_bytes);
    fnv1a64(&bytes)
}

/// The pure append-only store. It owns only a bounded resident suffix; the caller owns the full
/// durable tape. Sequence numbers and the head are store-assigned cursors, never money scalars.
#[derive(Debug, Clone)]
pub struct AppendLog {
    limits: Limits,
    resident: VecDeque<Entry>,
    head: u64,
    next_seq: u64,
    state: LogState,
}

impl AppendLog {
    #[must_use]
    pub fn new(limits: Limits) -> Self {
        AppendLog {
            limits,
            resident: VecDeque::new(),
            head: GENESIS_DIGEST,
            next_seq: 1,
            state: LogState::Open,
        }
    }

    #[must_use]
    pub fn limits(&self) -> Limits {
        self.limits
    }

    #[must_use]
    pub fn state(&self) -> &LogState {
        &self.state
    }

    /// Digest of the last appended entry, or [`GENESIS_DIGEST`] before the first one. Survives
    /// eviction, so the chain stays continuous even when the resident window is empty.
    #[must_use]
    pub fn head(&self) -> u64 {
        self.head
    }

    /// The sequence the next append will receive.
    #[must_use]
    pub fn next_seq(&self) -> u64 {
        self.next_seq
    }

    #[must_use]
    pub fn resident(&self) -> &VecDeque<Entry> {
        &self.resident
    }

    #[must_use]
    pub fn is_quarantined(&self) -> bool {
        matches!(&self.state, LogState::Quarantined(_))
    }

    /// Append one opaque payload. The store assigns the sequence and predecessor; the caller
    /// cannot supply either. On success the returned reference is immutable for the store's life.
    pub fn append(&mut self, payload: &[u8]) -> Result<&Entry, AppendError> {
        match &self.state {
            LogState::Quarantined(reason) => return Err(AppendError::Quarantined(reason.clone())),
            LogState::Sealed(_) => return Err(AppendError::Sealed),
            LogState::Open => {}
        }
        if payload.len() > self.limits.max_payload_bytes {
            return Err(AppendError::PayloadTooLarge {
                len: payload.len(),
                max: self.limits.max_payload_bytes,
            });
        }
        if self.next_seq == u64::MAX {
            return Err(AppendError::SequenceExhausted);
        }
        if self.resident.len() == self.limits.capacity {
            match self.limits.eviction {
                EvictionPolicy::RejectOnFull => {
                    return Err(AppendError::AtCapacity {
                        capacity: self.limits.capacity,
                    });
                }
                EvictionPolicy::DropOldest => {
                    self.resident.pop_front();
                }
            }
        }
        let seq = self.next_seq;
        let prev = self.head;
        let digest = entry_digest(prev, seq, payload);
        self.resident.push_back(Entry {
            seq,
            prev,
            digest,
            payload: payload.to_vec(),
        });
        self.head = digest;
        self.next_seq = seq + 1;
        Ok(self.resident.back().expect("entry was just pushed"))
    }

    /// Verify a whole durable tape against its recorded seal, then load the bounded resident
    /// suffix and advance the cursors. Any integrity failure quarantines this store and is returned
    /// as [`AppendError::Quarantined`]; nothing is loaded and no append can follow it.
    pub fn ingest(&mut self, tape: &[Entry], expected: &Seal) -> Result<(), AppendError> {
        match &self.state {
            LogState::Quarantined(reason) => return Err(AppendError::Quarantined(reason.clone())),
            LogState::Sealed(_) => return Err(AppendError::Sealed),
            LogState::Open => {}
        }
        if let Some((index, entry)) = tape
            .iter()
            .enumerate()
            .find(|(_, entry)| entry.payload.len() > self.limits.max_payload_bytes)
        {
            let reason = TapeError::PayloadTooLarge {
                index,
                len: entry.payload.len(),
                max: self.limits.max_payload_bytes,
            };
            self.state = LogState::Quarantined(reason.clone());
            return Err(AppendError::Quarantined(reason));
        }
        if let Err(reason) = verify_tape(tape, expected) {
            self.state = LogState::Quarantined(reason.clone());
            return Err(AppendError::Quarantined(reason));
        }
        let keep_from = tape.len().saturating_sub(self.limits.capacity);
        self.resident = tape[keep_from..].iter().cloned().collect();
        self.head = expected.head;
        self.next_seq = expected.count.saturating_add(1);
        self.state = LogState::Sealed(*expected);
        Ok(())
    }

    /// Explicit operator quarantine: a terminal refusal for this tape version. The first reason is
    /// kept so a later call cannot overwrite the evidence of why the tape was stopped.
    pub fn quarantine(&mut self, reason: TapeError) {
        if matches!(&self.state, LogState::Quarantined(_)) {
            return;
        }
        self.state = LogState::Quarantined(reason);
    }
}

#[cfg(test)]
mod tests {
    use super::{
        entry_digest, fnv1a64, replay, seal, state_digest, verify_tape, AppendError, AppendLog,
        Entry, EvictionPolicy, Limits, LogState, Seal, TapeError, GENESIS_DIGEST,
    };

    fn limits(capacity: usize, eviction: EvictionPolicy) -> Limits {
        Limits::new(16, capacity, eviction)
    }

    /// Collect a fresh store's appends into the caller-owned full tape, as a durable reader would.
    fn tape_of(capacity: usize, payloads: &[&[u8]]) -> (AppendLog, Vec<Entry>) {
        let mut log = AppendLog::new(limits(capacity, EvictionPolicy::DropOldest));
        let mut tape = Vec::new();
        for payload in payloads {
            tape.push(log.append(payload).expect("append").clone());
        }
        (log, tape)
    }

    #[test]
    fn fnv1a64_matches_known_answer_vectors() {
        assert_eq!(fnv1a64(b""), 0xcbf2_9ce4_8422_2325);
        assert_eq!(fnv1a64(b"a"), 0xaf63_dc4c_8601_ec8c);
        assert_eq!(fnv1a64(b"foobar"), 0x8594_4171_f739_67e8);
    }

    #[test]
    fn the_store_assigns_monotonic_sequences_from_one() {
        let (_log, tape) = tape_of(8, &[b"a", b"b", b"c"]);
        let seqs: Vec<u64> = tape.iter().map(Entry::seq).collect();
        assert_eq!(seqs, vec![1, 2, 3]);
        assert_eq!(tape[0].prev(), GENESIS_DIGEST);
        assert_eq!(tape[1].prev(), tape[0].digest());
        assert_eq!(tape[2].prev(), tape[1].digest());
    }

    #[test]
    fn an_entry_owns_its_payload_after_append() {
        let mut log = AppendLog::new(limits(4, EvictionPolicy::DropOldest));
        let mut payload = b"abc".to_vec();
        let appended = log.append(&payload).expect("append").clone();
        payload[0] = b'z';
        assert_eq!(appended.payload(), b"abc");
        assert_eq!(log.resident()[0].payload(), b"abc");
    }

    #[test]
    fn appends_only_extend_the_tail_in_order() {
        let (_log, tape) = tape_of(8, &[b"first", b"second", b"third"]);
        let payloads: Vec<&[u8]> = tape.iter().map(Entry::payload).collect();
        assert_eq!(payloads, vec![b"first".as_slice(), b"second", b"third"]);
    }

    #[test]
    fn drop_oldest_evicts_only_the_resident_suffix_but_keeps_the_chain() {
        let (mut log, tape) = tape_of(2, &[b"a", b"b", b"c", b"d"]);
        let resident: Vec<u64> = log.resident().iter().map(Entry::seq).collect();
        assert_eq!(resident, vec![3, 4]);
        assert_eq!(log.head(), tape[3].digest());
        assert_eq!(log.next_seq(), 5);
        // The caller's full tape is untouched by eviction and still verifies.
        assert_eq!(seal(&tape).expect("full tape seals").count, 4);
        // A further append continues the chain from the retained head cursor.
        let fifth = log.append(b"e").expect("append after eviction").clone();
        assert_eq!(fifth.seq(), 5);
        assert_eq!(fifth.prev(), tape[3].digest());
    }

    #[test]
    fn reject_on_full_refuses_without_changing_state() {
        let mut log = AppendLog::new(limits(2, EvictionPolicy::RejectOnFull));
        log.append(b"a").expect("append a");
        log.append(b"b").expect("append b");
        let head = log.head();
        let next_seq = log.next_seq();
        assert_eq!(
            log.append(b"c"),
            Err(AppendError::AtCapacity { capacity: 2 })
        );
        assert_eq!(log.head(), head);
        assert_eq!(log.next_seq(), next_seq);
        assert_eq!(log.resident().len(), 2);
    }

    #[test]
    fn an_oversized_payload_is_refused_without_poisoning_the_store() {
        let mut log = AppendLog::new(Limits::new(4, 4, EvictionPolicy::DropOldest));
        assert_eq!(
            log.append(b"too long"),
            Err(AppendError::PayloadTooLarge { len: 8, max: 4 })
        );
        assert_eq!(log.state(), &LogState::Open);
        log.append(b"ok").expect("valid append still works");
    }

    #[test]
    fn a_zero_capacity_store_is_not_constructible() {
        let result = std::panic::catch_unwind(|| limits(0, EvictionPolicy::DropOldest));
        assert!(result.is_err());
    }

    #[test]
    fn sealing_and_replaying_an_empty_tape_is_valid() {
        let empty: Vec<Entry> = Vec::new();
        let s = seal(&empty).expect("empty tape seals");
        assert_eq!(s.count, 0);
        assert_eq!(s.head, GENESIS_DIGEST);
        verify_tape(&empty, &s).expect("empty tape verifies");
        assert_eq!(replay(&empty).expect("empty replay"), Default::default());
    }

    #[test]
    fn a_clean_tape_seals_verifies_and_derives_state_deterministically() {
        let (_log, tape) = tape_of(8, &[b"a", b"bb", b"ccc"]);
        let s = seal(&tape).expect("seal");
        assert_eq!(s.count, 3);
        verify_tape(&tape, &s).expect("verify");
        let first = replay(&tape).expect("replay");
        let second = replay(&tape).expect("replay again");
        assert_eq!(first, second);
        assert_eq!(first.count, 3);
        assert_eq!(first.applied, vec![1, 2, 3]);
        assert_eq!(first.payload_bytes, 6);
        assert_eq!(state_digest(&first), state_digest(&second));
    }

    #[test]
    fn a_sequence_gap_is_detected_and_quarantines_the_ingesting_store() {
        let (_log, tape) = tape_of(8, &[b"a", b"b", b"c"]);
        let s = seal(&tape).expect("seal");
        let gapped = [tape[0].clone(), tape[2].clone()];
        let mut store = AppendLog::new(limits(8, EvictionPolicy::DropOldest));
        let expected = AppendError::Quarantined(TapeError::SequenceGap {
            index: 1,
            expected: 2,
            found: 3,
        });
        assert_eq!(store.ingest(&gapped, &s), Err(expected.clone()));
        assert!(store.is_quarantined());
        assert_eq!(store.append(b"x"), Err(expected));
    }

    #[test]
    fn a_changed_payload_is_detected_as_a_digest_mismatch() {
        let (_log, tape) = tape_of(8, &[b"a", b"b"]);
        let s = seal(&tape).expect("seal");
        let mut tampered = tape.clone();
        tampered[1].payload = b"X".to_vec();
        assert_eq!(
            verify_tape(&tampered, &s),
            Err(TapeError::DigestMismatch { index: 1 })
        );
        assert_eq!(
            replay(&tampered),
            Err(TapeError::DigestMismatch { index: 1 })
        );
    }

    #[test]
    fn a_broken_chain_link_is_detected_after_the_self_digest() {
        let (_log, tape) = tape_of(8, &[b"a", b"b"]);
        let mut broken = tape.clone();
        // Keep the entry's own digest valid for its (forged) prev, but break the tape link.
        broken[1].prev = GENESIS_DIGEST;
        broken[1].digest = entry_digest(GENESIS_DIGEST, 2, b"b");
        assert_eq!(
            verify_tape(&broken, &seal(&tape).expect("seal")),
            Err(TapeError::ChainLinkMismatch { index: 1 })
        );
    }

    #[test]
    fn verification_checks_the_chain_before_the_seal_and_count_before_head() {
        let (_log, tape) = tape_of(8, &[b"a", b"b"]);
        let s = seal(&tape).expect("seal");
        let wrong = Seal {
            count: 99,
            head: u64::MAX,
        };
        // Both seal fields wrong: count is reported first.
        assert_eq!(
            verify_tape(&tape, &wrong),
            Err(TapeError::CountMismatch {
                expected: 99,
                found: 2,
            })
        );
        // Count right, head wrong: head is reported.
        assert_eq!(
            verify_tape(
                &tape,
                &Seal {
                    count: s.count,
                    head: s.head ^ 1,
                }
            ),
            Err(TapeError::HeadMismatch {
                expected: s.head ^ 1,
                found: s.head,
            })
        );
        // A corrupt entry wins over a wrong seal: the chain is checked first.
        let mut tampered = tape.clone();
        tampered[0].payload = b"X".to_vec();
        assert_eq!(
            verify_tape(&tampered, &wrong),
            Err(TapeError::DigestMismatch { index: 0 })
        );
        // A truncated prefix verifies the chain clean and is caught only by the seal count.
        assert_eq!(
            verify_tape(&tape[..1], &s),
            Err(TapeError::CountMismatch {
                expected: 2,
                found: 1,
            })
        );
    }

    #[test]
    fn an_oversized_tape_entry_is_quarantined_during_ingest() {
        let mut big = AppendLog::new(limits(64, EvictionPolicy::DropOldest));
        let tape = vec![big.append(b"a").expect("append").clone()];
        let s = seal(&tape).expect("seal");
        let mut small = AppendLog::new(Limits::new(0, 4, EvictionPolicy::DropOldest));
        assert_eq!(
            small.ingest(&tape, &s),
            Err(AppendError::Quarantined(TapeError::PayloadTooLarge {
                index: 0,
                len: 1,
                max: 0,
            }))
        );
        assert!(small.is_quarantined());
    }

    #[test]
    fn ingest_loads_a_bounded_suffix_and_a_sealed_store_refuses_appends() {
        let (_log, tape) = tape_of(8, &[b"a", b"b", b"c", b"d"]);
        let s = seal(&tape).expect("seal");
        let mut store = AppendLog::new(limits(2, EvictionPolicy::DropOldest));
        store.ingest(&tape, &s).expect("ingest");
        assert_eq!(store.state(), &LogState::Sealed(s));
        let resident: Vec<u64> = store.resident().iter().map(Entry::seq).collect();
        assert_eq!(resident, vec![3, 4]);
        assert_eq!(store.head(), s.head);
        assert_eq!(store.next_seq(), 5);
        assert_eq!(store.append(b"e"), Err(AppendError::Sealed));
    }

    #[test]
    fn a_quarantined_store_also_refuses_ingest_and_keeps_the_first_reason() {
        let (_log, tape) = tape_of(8, &[b"a"]);
        let s = seal(&tape).expect("seal");
        let mut store = AppendLog::new(limits(8, EvictionPolicy::DropOldest));
        let first = TapeError::HeadMismatch {
            expected: 1,
            found: 2,
        };
        store.quarantine(first.clone());
        store.quarantine(TapeError::HeadMismatch {
            expected: 3,
            found: 4,
        });
        assert_eq!(store.state(), &LogState::Quarantined(first.clone()));
        assert_eq!(
            store.ingest(&tape, &s),
            Err(AppendError::Quarantined(first))
        );
    }

    #[test]
    fn a_dropped_or_reordered_tape_derives_a_different_state() {
        let (_log, tape) = tape_of(8, &[b"a", b"b", b"c"]);
        let full = replay(&tape).expect("full replay");

        let mut reordered = tape.clone();
        reordered.swap(0, 1);
        assert_eq!(
            replay(&reordered),
            Err(TapeError::SequenceGap {
                index: 0,
                expected: 1,
                found: 2,
            })
        );

        let shortened = &tape[..2];
        let short = replay(shortened).expect("prefix replays clean");
        assert_ne!(full, short);
        assert_ne!(state_digest(&full), state_digest(&short));
    }
}
