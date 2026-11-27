//! Recorded cases for the pure storage/replay kernel, mirroring the Python oracle.
//!
//! Oracle: `packages/core-py/src/ezchain_core/ledger.py` — `seal_entries`, `verify_seal`,
//! `ledger_digest`, `replay_balances`, `replay_position` — and the unkeyed-trace lessons in
//! ADR-0042 §"Amendment (review round 2, what the chain proves)". Only the pure, non-monetary
//! semantics are ported: sequencing, replay determinism and seal-verification order. Money
//! arithmetic and ledger write paths stay in Python (ADR-0050 rule 4, CONTRACT I1).

use ezchain_core::append_log::{
    entry_digest, replay, seal, state_digest, verify_tape, AppendLog, Entry, EvictionPolicy,
    Limits, Seal, TapeError, GENESIS_DIGEST,
};

fn tape(payloads: &[&[u8]]) -> Vec<Entry> {
    let mut log = AppendLog::new(Limits::new(64, 64, EvictionPolicy::DropOldest));
    payloads
        .iter()
        .map(|payload| log.append(payload).expect("append").clone())
        .collect()
}

#[test]
fn same_tape_seals_the_same_as_python_seal_entries() {
    let a = tape(&[b"alpha", b"beta", b"gamma"]);
    let b = tape(&[b"alpha", b"beta", b"gamma"]);
    let seal_a = seal(&a).expect("seal a");
    let seal_b = seal(&b).expect("seal b");
    assert_eq!(seal_a, seal_b);
    assert_eq!(seal_a.count, 3);
    assert_ne!(seal_a.head, GENESIS_DIGEST);
    verify_tape(&a, &seal_a).expect("verify a");
}

#[test]
fn verify_seal_refuses_a_changed_count_or_head() {
    let t = tape(&[b"alpha", b"beta"]);
    let recorded = seal(&t).expect("seal");
    assert_eq!(
        verify_tape(
            &t,
            &Seal {
                count: 3,
                ..recorded
            }
        ),
        Err(TapeError::CountMismatch {
            expected: 3,
            found: 2,
        })
    );
    assert_eq!(
        verify_tape(
            &t,
            &Seal {
                head: recorded.head ^ 1,
                ..recorded
            }
        ),
        Err(TapeError::HeadMismatch {
            expected: recorded.head ^ 1,
            found: recorded.head,
        })
    );
}

#[test]
fn replay_is_deterministic_for_the_same_tape() {
    let t = tape(&[b"one", b"two", b"three"]);
    let first = replay(&t).expect("replay");
    let second = replay(&t).expect("replay again");
    assert_eq!(first, second);
    assert_eq!(state_digest(&first), state_digest(&second));
    assert_eq!(first.applied, vec![1, 2, 3]);
}

#[test]
fn changing_any_entry_changes_the_head() {
    let original = tape(&[b"alpha", b"beta"]);
    let edited = tape(&[b"alpha", b"BETA"]);
    assert_ne!(
        seal(&original).expect("seal").head,
        seal(&edited).expect("seal").head
    );
    // A different first entry changes every following link, not only its own digest.
    let other = tape(&[b"ALPHA", b"beta"]);
    assert_ne!(
        seal(&original).expect("seal").head,
        seal(&other).expect("seal").head
    );
}

#[test]
fn a_prefix_is_integrity_clean_but_replay_differs() {
    let t = tape(&[b"alpha", b"beta", b"gamma"]);
    let recorded = seal(&t).expect("seal");
    let prefix = &t[..2];
    // Integrity is not completeness: the chain of a prefix verifies on its own …
    seal(prefix).expect("prefix chain verifies");
    // … but the recorded seal catches the truncation, and replay derives a different state.
    assert_eq!(
        verify_tape(prefix, &recorded),
        Err(TapeError::CountMismatch {
            expected: 3,
            found: 2,
        })
    );
    assert_ne!(
        replay(prefix).expect("prefix replay"),
        replay(&t).expect("full replay")
    );
}

#[test]
fn the_digest_is_unkeyed_tamper_evidence_not_a_signature() {
    // There is no key parameter: the digest is a pure function of public bytes, so anyone holding
    // the tape can recompute it. It detects corruption, not authorship (ADR-0051 §"Message
    // contract"). This test records that property rather than claiming a security guarantee.
    assert_eq!(
        entry_digest(GENESIS_DIGEST, 1, b"x"),
        entry_digest(GENESIS_DIGEST, 1, b"x")
    );
    assert_ne!(
        entry_digest(GENESIS_DIGEST, 1, b"x"),
        entry_digest(GENESIS_DIGEST, 2, b"x")
    );
}
