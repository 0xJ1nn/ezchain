//! Deterministic delivery trace and its pure replay for the gossip protocol kernel.
//!
//! Oracle: ADR-0051 §"State contract and replay" and CONTRACT I9 ("Replay or it didn't happen").
//! The trace is the append-only input record of what the kernel decided for each envelope. It is
//! not money state: [`DeliveryTrace::replay`] folds the recorded events into a derived summary and a
//! deterministic digest, so two nodes that processed the same envelopes in the same order produce
//! the same fingerprint, while a dropped, reordered or altered event changes it. Replay performs
//! no I/O, holds no keys and writes nothing.
//!
//! This is a protocol-kernel trace, not a gossip transport log: it records decisions, not sockets.

use crate::envelope::{self, Rejection};

/// A typed reason an envelope was refused by the kernel, in check order (ADR-0051's reproducible
/// reason discipline). Carried on the trace so a refusal is replayable (CONTRACT I9).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Refusal {
    InvalidEnvelope(Rejection),
    UnknownPeer,
    SetVersionMismatch { expected: String, got: String },
    Duplicate,
    QueueFull { capacity: usize },
}

/// One refused delivery, with the data needed to replay it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RefusalRecord {
    pub sender: String,
    pub id: Option<u64>,
    pub reason: Refusal,
}

/// One recorded decision. `Accepted` carries the fanout it chose; `Refused` carries why.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DeliveryEvent {
    Accepted {
        id: u64,
        sender: String,
        seq: u64,
        fanout: Vec<String>,
    },
    Refused(RefusalRecord),
}

/// An append-only trace of delivery decisions.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct DeliveryTrace {
    events: Vec<DeliveryEvent>,
}

impl DeliveryTrace {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn record(&mut self, event: DeliveryEvent) {
        self.events.push(event);
    }

    pub fn events(&self) -> &[DeliveryEvent] {
        &self.events
    }

    /// Replay the trace into a derived summary and a deterministic digest. Identical traces yield
    /// identical summaries; any change to an event changes the digest.
    pub fn replay(&self) -> TraceSummary {
        let mut accepted_ids = Vec::new();
        let mut refused = Vec::new();
        let mut forwarded_to = Vec::new();
        for event in &self.events {
            match event {
                DeliveryEvent::Accepted { id, fanout, .. } => {
                    accepted_ids.push(*id);
                    forwarded_to.extend(fanout.iter().cloned());
                }
                DeliveryEvent::Refused(record) => refused.push(record.clone()),
            }
        }
        let mut encoded = Vec::new();
        encode_summary(&mut encoded, &accepted_ids, &refused, &forwarded_to);
        TraceSummary {
            accepted_ids,
            refused,
            forwarded_to,
            digest: envelope::digest_bytes(&encoded),
        }
    }
}

/// The derived result of replaying a trace. All fields are public so a caller can compare two
/// nodes' projections; `digest` is a single deterministic fingerprint of all of them.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TraceSummary {
    pub accepted_ids: Vec<u64>,
    pub refused: Vec<RefusalRecord>,
    pub forwarded_to: Vec<String>,
    pub digest: u64,
}

// The encoding below is length-prefixed and section-counted so different field boundaries cannot
// produce the same bytes (the same discipline as `envelope::canonical_bytes`).

fn push_u64(out: &mut Vec<u8>, value: u64) {
    out.extend_from_slice(&value.to_le_bytes());
}

fn push_bytes(out: &mut Vec<u8>, bytes: &[u8]) {
    push_u64(out, bytes.len() as u64);
    out.extend_from_slice(bytes);
}

fn rejection_code(rejection: Rejection) -> u64 {
    match rejection {
        Rejection::UnsupportedSchema => 1,
        Rejection::UnknownKind => 2,
        Rejection::SenderEmpty => 3,
        Rejection::SetVersionEmpty => 4,
        Rejection::SequenceZero => 5,
        Rejection::PayloadEmpty => 6,
        Rejection::PayloadTooLarge => 7,
        Rejection::EnvelopeTooLarge => 8,
        Rejection::DigestMismatch => 9,
    }
}

fn encode_refusal(out: &mut Vec<u8>, refusal: &Refusal) {
    match refusal {
        Refusal::InvalidEnvelope(rejection) => {
            push_u64(out, 1);
            push_u64(out, rejection_code(*rejection));
        }
        Refusal::UnknownPeer => push_u64(out, 2),
        Refusal::SetVersionMismatch { expected, got } => {
            push_u64(out, 3);
            push_bytes(out, expected.as_bytes());
            push_bytes(out, got.as_bytes());
        }
        Refusal::Duplicate => push_u64(out, 4),
        Refusal::QueueFull { capacity } => {
            push_u64(out, 5);
            push_u64(out, *capacity as u64);
        }
    }
}

fn encode_summary(
    out: &mut Vec<u8>,
    accepted_ids: &[u64],
    refused: &[RefusalRecord],
    forwarded_to: &[String],
) {
    push_u64(out, accepted_ids.len() as u64);
    for id in accepted_ids {
        push_u64(out, *id);
    }
    push_u64(out, refused.len() as u64);
    for record in refused {
        push_bytes(out, record.sender.as_bytes());
        match record.id {
            Some(id) => {
                push_u64(out, 1);
                push_u64(out, id);
            }
            None => push_u64(out, 0),
        }
        encode_refusal(out, &record.reason);
    }
    push_u64(out, forwarded_to.len() as u64);
    for peer in forwarded_to {
        push_bytes(out, peer.as_bytes());
    }
}

#[cfg(test)]
mod tests {
    use super::{DeliveryEvent, DeliveryTrace, Refusal, RefusalRecord};
    use crate::envelope::Rejection;

    fn sample_trace() -> DeliveryTrace {
        let mut trace = DeliveryTrace::new();
        trace.record(DeliveryEvent::Accepted {
            id: 11,
            sender: "node-a".to_string(),
            seq: 1,
            fanout: vec!["node-b".to_string(), "node-c".to_string()],
        });
        trace.record(DeliveryEvent::Refused(RefusalRecord {
            sender: "node-x".to_string(),
            id: None,
            reason: Refusal::InvalidEnvelope(Rejection::DigestMismatch),
        }));
        trace.record(DeliveryEvent::Refused(RefusalRecord {
            sender: "node-a".to_string(),
            id: Some(11),
            reason: Refusal::Duplicate,
        }));
        trace
    }

    #[test]
    fn replay_is_deterministic_and_reconstructs_the_projection() {
        let a = sample_trace().replay();
        let b = sample_trace().replay();
        assert_eq!(a, b);
        assert_eq!(a.accepted_ids, vec![11]);
        assert_eq!(a.forwarded_to, vec!["node-b", "node-c"]);
        assert_eq!(a.refused.len(), 2);
        assert_eq!(
            a.refused[0].reason,
            Refusal::InvalidEnvelope(Rejection::DigestMismatch)
        );
        assert_eq!(a.refused[1].reason, Refusal::Duplicate);
    }

    #[test]
    fn replaying_an_empty_trace_yields_the_empty_projection() {
        let summary = DeliveryTrace::new().replay();
        assert!(summary.accepted_ids.is_empty());
        assert!(summary.refused.is_empty());
        assert!(summary.forwarded_to.is_empty());
        assert_eq!(summary.digest, DeliveryTrace::new().replay().digest);
    }

    #[test]
    fn a_reordered_event_changes_the_digest() {
        let trace = sample_trace();
        let events = trace.events().to_vec();
        let mut reordered = DeliveryTrace::new();
        for event in events.into_iter().rev() {
            reordered.record(event);
        }
        assert_ne!(trace.replay().digest, reordered.replay().digest);
    }

    #[test]
    fn a_dropped_event_changes_the_digest() {
        let full = sample_trace().replay().digest;
        let trace = sample_trace();
        let events = trace.events().to_vec();
        let mut dropped = DeliveryTrace::new();
        for event in events.into_iter().take(2) {
            dropped.record(event);
        }
        assert_ne!(full, dropped.replay().digest);
    }

    #[test]
    fn an_altered_event_changes_the_digest() {
        let full = sample_trace().replay().digest;
        let altered = sample_trace();
        let mut events = altered.events().to_vec();
        if let DeliveryEvent::Accepted { id, .. } = &mut events[0] {
            *id += 1;
        }
        let mut rebuilt = DeliveryTrace::new();
        for event in events {
            rebuilt.record(event);
        }
        assert_ne!(full, rebuilt.replay().digest);
    }

    #[test]
    fn every_refusal_variant_is_replayable_and_distinct() {
        let refusals = vec![
            Refusal::InvalidEnvelope(Rejection::UnsupportedSchema),
            Refusal::UnknownPeer,
            Refusal::SetVersionMismatch {
                expected: "set-2".to_string(),
                got: "set-1".to_string(),
            },
            Refusal::Duplicate,
            Refusal::QueueFull { capacity: 4 },
        ];
        let mut digests = Vec::new();
        for reason in refusals {
            let mut trace = DeliveryTrace::new();
            trace.record(DeliveryEvent::Refused(RefusalRecord {
                sender: "node-a".to_string(),
                id: Some(7),
                reason,
            }));
            let summary = trace.replay();
            assert_eq!(summary.refused.len(), 1);
            assert_eq!(summary.refused[0].id, Some(7));
            digests.push(summary.digest);
        }
        // Each distinct refusal produces a distinct fingerprint.
        for i in 0..digests.len() {
            for j in (i + 1)..digests.len() {
                assert_ne!(digests[i], digests[j]);
            }
        }
    }
}
