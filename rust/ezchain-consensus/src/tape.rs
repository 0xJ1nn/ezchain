//! Deterministic tape transitions and replay over a non-monetary register.
//!
//! Oracle: ADR-0051 §§ "State contract and replay", "Lifecycle" and "Fork and failure handling" in
//! `docs/DECISIONS.md`. Applying an event is the single writer of state; readers project the
//! resulting state and never re-derive it from parallel flags. Every refusal leaves state unchanged
//! (fail closed, CONTRACT I3).

use std::collections::BTreeMap;

/// A non-monetary command over the replicated string register (ADR-0051 §"State contract").
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Command {
    Set { key: String, value: String },
    Delete { key: String },
}

/// One recorded event on the tape. `id` is the opaque content id supplied by a validated envelope;
/// `sender`, `set_version` and `seq` are the fields the transition checks.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TapeEvent {
    pub id: u64,
    pub sender: String,
    pub set_version: String,
    pub seq: u64,
    pub command: Command,
}

/// An ordered tape bound to one validator-set version.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Tape {
    pub set_version: String,
    pub events: Vec<TapeEvent>,
}

/// The deterministic projection of an accepted tape (ADR-0051 §"State contract"). Ordered maps
/// make two nodes' states compare and serialise identically.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TapeState {
    /// Accepted content ids in tape order.
    pub applied: Vec<u64>,
    /// The register itself: opaque strings, with no monetary meaning and no arithmetic.
    pub registers: BTreeMap<String, String>,
    /// Highest accepted sequence per sender.
    pub last_seq: BTreeMap<String, u64>,
}

impl TapeState {
    /// The empty state a replay starts from.
    pub fn new() -> Self {
        TapeState {
            applied: Vec::new(),
            registers: BTreeMap::new(),
            last_seq: BTreeMap::new(),
        }
    }
}

impl Default for TapeState {
    fn default() -> Self {
        Self::new()
    }
}

/// A typed refusal of a tape event or tape (ADR-0051 §"State contract and replay").
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReplayError {
    EmptySetVersion,
    SetVersionMismatch,
    EmptySender,
    EmptyKey,
    SequenceNotMonotonic,
    DuplicateId,
}

fn push_u64(out: &mut Vec<u8>, value: u64) {
    out.extend_from_slice(&value.to_le_bytes());
}

fn push_str(out: &mut Vec<u8>, value: &str) {
    push_u64(out, value.len() as u64);
    out.extend_from_slice(value.as_bytes());
}

/// Apply one event to a state, returning the next state. This is the only writer of state; a
/// refusal returns no new state and the caller keeps its current one unchanged.
pub fn apply(
    state: &TapeState,
    event: &TapeEvent,
    set_version: &str,
) -> Result<TapeState, ReplayError> {
    if set_version.is_empty() || event.set_version.is_empty() {
        return Err(ReplayError::EmptySetVersion);
    }
    if event.set_version != set_version {
        return Err(ReplayError::SetVersionMismatch);
    }
    if event.sender.is_empty() {
        return Err(ReplayError::EmptySender);
    }
    let key = match &event.command {
        Command::Set { key, .. } => key,
        Command::Delete { key } => key,
    };
    if key.is_empty() {
        return Err(ReplayError::EmptyKey);
    }
    match state.last_seq.get(&event.sender) {
        Some(last) if event.seq <= *last => return Err(ReplayError::SequenceNotMonotonic),
        None if event.seq == 0 => return Err(ReplayError::SequenceNotMonotonic),
        _ => {}
    }
    if state.applied.contains(&event.id) {
        return Err(ReplayError::DuplicateId);
    }

    let mut next = state.clone();
    next.applied.push(event.id);
    next.last_seq.insert(event.sender.clone(), event.seq);
    match &event.command {
        Command::Set { key, value } => {
            next.registers.insert(key.clone(), value.clone());
        }
        Command::Delete { key } => {
            next.registers.remove(key);
        }
    }
    Ok(next)
}

/// Replay a whole tape from the empty state with the same transition [`apply`] uses. An identical
/// tape always yields an identical state; a changed, dropped or reordered tape yields a different
/// state or a typed refusal.
pub fn replay(tape: &Tape) -> Result<TapeState, ReplayError> {
    if tape.set_version.is_empty() {
        return Err(ReplayError::EmptySetVersion);
    }
    let mut state = TapeState::new();
    for event in &tape.events {
        state = apply(&state, event, &tape.set_version)?;
    }
    Ok(state)
}

/// A deterministic digest of a replayed state, so two nodes can compare their view without
/// exchanging the whole state. FNV-1a over a canonical, length-prefixed encoding of the ordered
/// maps; it is a comparison aid, not a signature (ADR-0051 §"Message contract").
pub fn state_digest(state: &TapeState) -> u64 {
    const OFFSET: u64 = 0xcbf2_9ce4_8422_2325;
    const PRIME: u64 = 0x0000_0100_0000_01b3;
    let mut bytes = Vec::new();
    push_u64(&mut bytes, state.applied.len() as u64);
    for id in &state.applied {
        push_u64(&mut bytes, *id);
    }
    push_u64(&mut bytes, state.registers.len() as u64);
    for (key, value) in &state.registers {
        push_str(&mut bytes, key);
        push_str(&mut bytes, value);
    }
    push_u64(&mut bytes, state.last_seq.len() as u64);
    for (sender, seq) in &state.last_seq {
        push_str(&mut bytes, sender);
        push_u64(&mut bytes, *seq);
    }
    let mut hash = OFFSET;
    for byte in bytes {
        hash ^= u64::from(byte);
        hash = hash.wrapping_mul(PRIME);
    }
    hash
}

#[cfg(test)]
mod tests {
    use super::{apply, replay, state_digest, Command, ReplayError, Tape, TapeEvent, TapeState};

    // Oracle: ADR-0051 §§ "State contract and replay", "Lifecycle" and "Fork and failure handling"
    // (docs/DECISIONS.md). These cases are the written contract itself; there is no Python original
    // to record from.

    fn event(id: u64, sender: &str, seq: u64, command: Command) -> TapeEvent {
        TapeEvent {
            id,
            sender: sender.to_string(),
            set_version: "set-1".to_string(),
            seq,
            command,
        }
    }

    fn set(id: u64, sender: &str, seq: u64, key: &str, value: &str) -> TapeEvent {
        event(
            id,
            sender,
            seq,
            Command::Set {
                key: key.to_string(),
                value: value.to_string(),
            },
        )
    }

    fn tape(events: Vec<TapeEvent>) -> Tape {
        Tape {
            set_version: "set-1".to_string(),
            events,
        }
    }

    fn sample_tape() -> Tape {
        tape(vec![
            set(11, "node-a", 1, "alpha", "one"),
            set(22, "node-b", 1, "beta", "two"),
            event(
                33,
                "node-a",
                2,
                Command::Delete {
                    key: "alpha".to_string(),
                },
            ),
            set(44, "node-b", 2, "gamma", "three"),
        ])
    }

    #[test]
    fn an_empty_tape_replays_to_the_empty_state() {
        let state = replay(&tape(Vec::new())).expect("empty tape replays");
        assert_eq!(state, TapeState::new());
        assert!(state.applied.is_empty());
        assert!(state.registers.is_empty());
    }

    #[test]
    fn replay_is_repeatable_and_matches_stepwise_apply() {
        let tape = sample_tape();
        let first = replay(&tape).expect("first replay");
        let second = replay(&tape).expect("second replay");
        assert_eq!(first, second);
        assert_eq!(state_digest(&first), state_digest(&second));

        let mut stepwise = TapeState::new();
        for event in &tape.events {
            stepwise = apply(&stepwise, event, &tape.set_version).expect("stepwise apply");
        }
        assert_eq!(stepwise, first);
    }

    #[test]
    fn set_and_delete_project_the_register() {
        let state = replay(&sample_tape()).expect("replay");
        assert_eq!(state.applied, vec![11, 22, 33, 44]);
        assert_eq!(state.registers.get("alpha"), None);
        assert_eq!(state.registers.get("beta").map(String::as_str), Some("two"));
        assert_eq!(
            state.registers.get("gamma").map(String::as_str),
            Some("three")
        );
        assert_eq!(state.last_seq.get("node-a"), Some(&2));
        assert_eq!(state.last_seq.get("node-b"), Some(&2));
    }

    #[test]
    fn a_duplicate_content_id_is_refused_without_changing_state() {
        let mut events = sample_tape().events;
        events.push(set(22, "node-b", 2, "delta", "four"));
        let state = replay(&tape(events[..3].to_vec())).expect("prefix");
        let duplicate = set(22, "node-c", 1, "delta", "four");
        assert_eq!(
            apply(&state, &duplicate, "set-1"),
            Err(ReplayError::DuplicateId)
        );
    }

    #[test]
    fn a_regressive_or_zero_sequence_is_refused() {
        let state = replay(&tape(sample_tape().events[..3].to_vec())).expect("prefix");
        let older = set(55, "node-a", 1, "delta", "four");
        assert_eq!(
            apply(&state, &older, "set-1"),
            Err(ReplayError::SequenceNotMonotonic)
        );
        let zero = set(56, "node-c", 0, "delta", "four");
        assert_eq!(
            apply(&state, &zero, "set-1"),
            Err(ReplayError::SequenceNotMonotonic)
        );
    }

    #[test]
    fn an_event_from_another_set_version_is_refused() {
        let mut shifted = sample_tape();
        shifted.events[1].set_version = "set-2".to_string();
        assert_eq!(replay(&shifted), Err(ReplayError::SetVersionMismatch));

        let mut unnamed = sample_tape();
        unnamed.set_version.clear();
        assert_eq!(replay(&unnamed), Err(ReplayError::EmptySetVersion));
    }

    #[test]
    fn empty_sender_and_empty_key_fail_closed() {
        let state = TapeState::new();
        let senderless = set(11, "", 1, "alpha", "one");
        assert_eq!(
            apply(&state, &senderless, "set-1"),
            Err(ReplayError::EmptySender)
        );
        let keyless = set(11, "node-a", 1, "", "one");
        assert_eq!(apply(&state, &keyless, "set-1"), Err(ReplayError::EmptyKey));
        let keyless_delete = event(11, "node-a", 1, Command::Delete { key: String::new() });
        assert_eq!(
            apply(&state, &keyless_delete, "set-1"),
            Err(ReplayError::EmptyKey)
        );
    }

    #[test]
    fn a_dropped_event_produces_a_different_state_and_digest() {
        let full = replay(&sample_tape()).expect("full replay");
        let mut shortened = sample_tape();
        shortened.events.remove(1);
        let dropped = replay(&shortened).expect("short replay");
        assert_ne!(full, dropped);
        assert_ne!(state_digest(&full), state_digest(&dropped));
    }

    #[test]
    fn reordering_within_a_sender_is_refused() {
        let mut reordered = sample_tape();
        reordered.events.swap(0, 2);
        assert_eq!(replay(&reordered), Err(ReplayError::SequenceNotMonotonic));
    }

    #[test]
    fn the_register_is_deterministic_across_key_insertion_order() {
        let a = tape(vec![
            set(11, "node-a", 1, "alpha", "one"),
            set(22, "node-a", 2, "beta", "two"),
        ]);
        let b = tape(vec![
            set(44, "node-b", 1, "beta", "two"),
            set(55, "node-b", 2, "alpha", "one"),
        ]);
        let state_a = replay(&a).expect("replay a");
        let state_b = replay(&b).expect("replay b");
        assert_eq!(state_a.registers, state_b.registers);
    }
}
