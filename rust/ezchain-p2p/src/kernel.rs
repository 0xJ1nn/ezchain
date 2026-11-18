//! The pure gossip protocol kernel: admission, deduplication, fanout and a replayable trace.
//!
//! Oracle: ADR-0051 (`docs/DECISIONS.md`) — especially §§"Validator and deployment model",
//! "Message contract", "Lifecycle" and "State contract and replay". This module composes the
//! envelope validator, the bounded peer set, the FIFO deduplicator, the bounded inbound queue and
//! the deterministic fanout rule into one pure function over envelopes.
//!
//! **This is a protocol kernel, not a transport.** There are no sockets, no listeners, no async
//! runtime and no external dependency. It does not discover peers, connect, handshake, authenticate
//! a node id, sign anything, persist anything or move a byte on a network. Live money and vault keys
//! remain owner-blocked (ADR-0026, ADR-0035, ADR-0050 clause 3, ADR-0051 §"Live money and keys"):
//! this kernel carries no keys, writes no ledger and cannot arm live mode.

use crate::dedup::SeenIds;
use crate::envelope::{self, Envelope, Limits};
use crate::fanout::select_fanout;
use crate::peer::{PeerId, ValidatorSet};
use crate::queue::{InboundQueue, QueueRejection};
use crate::trace::{DeliveryEvent, DeliveryTrace, Refusal, RefusalRecord};

/// Typed refusal from constructing a kernel.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KernelError {
    SelfNotInSet,
    ZeroDedupCapacity,
}

/// Every policy knob is a caller parameter. ADR-0051 is Proposed and decides no concrete set
/// size, quorum number or fanout; hardcoding one here would violate CONTRACT I7.
#[derive(Debug, Clone)]
pub struct KernelConfig {
    /// This node's opaque id; it must be a member of `validators`.
    pub self_id: PeerId,
    pub validators: ValidatorSet,
    pub limits: Limits,
    pub dedup_capacity: usize,
    pub queue_capacity: usize,
    pub fanout: usize,
}

/// A message admitted to the bounded inbound queue. Queued items are caller-visible so the
/// control plane can drain them; this kernel never sends them anywhere itself.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct QueuedMessage {
    pub id: u64,
    pub sender: PeerId,
    pub seq: u64,
}

/// The successful result of handling one envelope.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Delivered {
    pub id: u64,
    pub fanout: Vec<PeerId>,
}

/// A pure, deterministic gossip protocol kernel.
#[derive(Debug)]
pub struct GossipKernel {
    validators: ValidatorSet,
    limits: Limits,
    seen: SeenIds,
    inbound: InboundQueue<QueuedMessage>,
    fanout: usize,
    cursor: u64,
    trace: DeliveryTrace,
}

impl GossipKernel {
    pub fn new(config: KernelConfig) -> Result<Self, KernelError> {
        if config.dedup_capacity == 0 {
            return Err(KernelError::ZeroDedupCapacity);
        }
        if !config.validators.contains(&config.self_id) {
            return Err(KernelError::SelfNotInSet);
        }
        let seen =
            SeenIds::new(config.dedup_capacity).map_err(|_| KernelError::ZeroDedupCapacity)?;
        Ok(Self {
            validators: config.validators,
            limits: config.limits,
            seen,
            inbound: InboundQueue::new(config.queue_capacity),
            fanout: config.fanout,
            cursor: 0,
            trace: DeliveryTrace::new(),
        })
    }

    /// Admit one envelope. Checks run in a fixed order and every refusal is recorded on the trace
    /// and changes no state (fail closed, CONTRACT I3):
    /// 1. envelope validation (typed [`Refusal::InvalidEnvelope`]);
    /// 2. sender membership ([`Refusal::UnknownPeer`]);
    /// 3. set-version membership ([`Refusal::SetVersionMismatch`], ADR-0051 §"Validator model");
    /// 4. duplicate content id ([`Refusal::Duplicate`]);
    /// 5. bounded inbound queue ([`Refusal::QueueFull`]).
    ///
    /// On success the message is queued, its id marked seen, the fanout selected with the current
    /// cursor and the whole decision appended to the trace.
    pub fn handle(&mut self, envelope: &Envelope) -> Result<Delivered, Refusal> {
        let id = match envelope::validate(envelope, self.limits) {
            Ok(id) => id,
            Err(rejection) => {
                self.refuse(
                    envelope.sender.clone(),
                    None,
                    Refusal::InvalidEnvelope(rejection),
                );
                return Err(Refusal::InvalidEnvelope(rejection));
            }
        };
        if !self.validators.contains(&envelope.sender) {
            let reason = Refusal::UnknownPeer;
            self.refuse(envelope.sender.clone(), Some(id), reason.clone());
            return Err(reason);
        }
        if envelope.set_version != self.validators.version() {
            let reason = Refusal::SetVersionMismatch {
                expected: self.validators.version().to_string(),
                got: envelope.set_version.clone(),
            };
            self.refuse(envelope.sender.clone(), Some(id), reason.clone());
            return Err(reason);
        }
        if self.seen.contains(id) {
            let reason = Refusal::Duplicate;
            self.refuse(envelope.sender.clone(), Some(id), reason.clone());
            return Err(reason);
        }
        let queued = QueuedMessage {
            id,
            sender: envelope.sender.clone(),
            seq: envelope.seq,
        };
        match self.inbound.try_push(queued) {
            Ok(()) => {}
            Err(QueueRejection::Full { capacity }) => {
                let reason = Refusal::QueueFull { capacity };
                self.refuse(envelope.sender.clone(), Some(id), reason.clone());
                return Err(reason);
            }
        }
        // Mark seen only after admission, so a queue-full refusal does not suppress a retry.
        self.seen.offer(id);
        let fanout = select_fanout(
            self.validators.peers(),
            &envelope.sender,
            self.fanout,
            self.cursor,
        );
        self.cursor = self.cursor.wrapping_add(1);
        self.trace.record(DeliveryEvent::Accepted {
            id,
            sender: envelope.sender.clone(),
            seq: envelope.seq,
            fanout: fanout.clone(),
        });
        Ok(Delivered { id, fanout })
    }

    fn refuse(&mut self, sender: PeerId, id: Option<u64>, reason: Refusal) {
        self.trace
            .record(DeliveryEvent::Refused(RefusalRecord { sender, id, reason }));
    }

    pub fn validators(&self) -> &ValidatorSet {
        &self.validators
    }

    pub fn trace(&self) -> &DeliveryTrace {
        &self.trace
    }

    /// Consume the kernel and return its trace for persistence or comparison between nodes.
    pub fn into_trace(self) -> DeliveryTrace {
        self.trace
    }

    /// Drain admitted messages to the caller. No network I/O happens here.
    pub fn drain_inbound(&mut self) -> Vec<QueuedMessage> {
        let mut drained = Vec::new();
        while let Some(message) = self.inbound.pop() {
            drained.push(message);
        }
        drained
    }

    pub fn inbound_len(&self) -> usize {
        self.inbound.len()
    }

    pub fn cursor(&self) -> u64 {
        self.cursor
    }
}

#[cfg(test)]
mod tests {
    use super::{GossipKernel, KernelConfig, KernelError};
    use crate::envelope::{envelope_id, Envelope, Limits, Rejection, SCHEMA};
    use crate::peer::ValidatorSet;
    use crate::trace::{DeliveryEvent, Refusal};

    fn limits() -> Limits {
        Limits {
            max_envelope_bytes: 1024,
            max_payload_bytes: 256,
        }
    }

    fn peer(id: &str) -> String {
        id.to_string()
    }

    fn set() -> ValidatorSet {
        ValidatorSet::new(
            "set-1",
            ["node-a", "node-b", "node-c", "node-d"]
                .iter()
                .map(|s| peer(s)),
            4,
        )
        .unwrap()
    }

    fn envelope(sender: &str, set_version: &str, seq: u64) -> Envelope {
        let mut e = Envelope {
            schema: SCHEMA.to_string(),
            kind: "tape_event".to_string(),
            sender: sender.to_string(),
            set_version: set_version.to_string(),
            seq,
            payload: vec![seq as u8, 2, 3],
            timestamp: 1_700_000_000 + seq,
            digest: 0,
        };
        e.digest = envelope_id(&e);
        e
    }

    fn kernel(dedup_capacity: usize, queue_capacity: usize, fanout: usize) -> GossipKernel {
        GossipKernel::new(KernelConfig {
            self_id: peer("node-a"),
            validators: set(),
            limits: limits(),
            dedup_capacity,
            queue_capacity,
            fanout,
        })
        .unwrap()
    }

    #[test]
    fn a_valid_envelope_is_delivered_with_deterministic_fanout() {
        let mut k = kernel(8, 8, 2);
        let delivered = k.handle(&envelope("node-a", "set-1", 1)).unwrap();
        assert_eq!(delivered.id, envelope_id(&envelope("node-a", "set-1", 1)));
        assert_eq!(delivered.fanout, vec!["node-b", "node-c"]);
        assert_eq!(k.inbound_len(), 1);
        assert_eq!(k.cursor(), 1);
        assert_eq!(k.drain_inbound().len(), 1);
        assert!(k.inbound_len() == 0);
    }

    #[test]
    fn a_duplicate_envelope_is_suppressed_and_recorded() {
        let mut k = kernel(8, 8, 2);
        let e = envelope("node-a", "set-1", 1);
        assert!(k.handle(&e).is_ok());
        assert_eq!(k.handle(&e), Err(Refusal::Duplicate));
        assert_eq!(k.inbound_len(), 1);
        assert_eq!(k.trace().events().len(), 2);
        match &k.trace().events()[1] {
            DeliveryEvent::Refused(record) => assert_eq!(record.reason, Refusal::Duplicate),
            other => panic!("expected a refusal, got {other:?}"),
        }
    }

    #[test]
    fn a_distinct_envelope_from_the_same_sender_is_not_a_duplicate() {
        let mut k = kernel(8, 8, 1);
        assert!(k.handle(&envelope("node-a", "set-1", 1)).is_ok());
        assert!(k.handle(&envelope("node-a", "set-1", 2)).is_ok());
        assert_eq!(k.inbound_len(), 2);
    }

    #[test]
    fn an_unknown_peer_is_refused_with_no_state_change() {
        let mut k = kernel(8, 8, 2);
        assert_eq!(
            k.handle(&envelope("node-z", "set-1", 1)),
            Err(Refusal::UnknownPeer)
        );
        assert_eq!(k.inbound_len(), 0);
        assert_eq!(k.cursor(), 0);
        assert_eq!(k.trace().replay().accepted_ids.len(), 0);
        assert_eq!(k.trace().replay().refused.len(), 1);
    }

    #[test]
    fn a_foreign_set_version_is_refused() {
        let mut k = kernel(8, 8, 2);
        let reason = k.handle(&envelope("node-a", "set-2", 1)).unwrap_err();
        assert_eq!(
            reason,
            Refusal::SetVersionMismatch {
                expected: "set-1".to_string(),
                got: "set-2".to_string(),
            }
        );
        assert_eq!(k.inbound_len(), 0);
    }

    #[test]
    fn an_invalid_envelope_is_refused_with_its_typed_reason() {
        let mut k = kernel(8, 8, 2);
        let mut e = envelope("node-a", "set-1", 1);
        e.digest ^= 1;
        assert_eq!(
            k.handle(&e),
            Err(Refusal::InvalidEnvelope(Rejection::DigestMismatch))
        );
        assert_eq!(k.inbound_len(), 0);
    }

    #[test]
    fn a_full_queue_refuses_and_does_not_mark_the_id_seen() {
        let mut k = kernel(8, 1, 1);
        assert!(k.handle(&envelope("node-a", "set-1", 1)).is_ok());
        assert_eq!(
            k.handle(&envelope("node-b", "set-1", 2)),
            Err(Refusal::QueueFull { capacity: 1 })
        );
        assert_eq!(k.inbound_len(), 1);
        // The refused id was not marked seen: draining the queue and retrying succeeds.
        assert_eq!(k.drain_inbound().len(), 1);
        assert!(k.handle(&envelope("node-b", "set-1", 2)).is_ok());
    }

    #[test]
    fn fanout_rotates_with_the_cursor_and_never_targets_the_sender() {
        let mut k = kernel(8, 8, 2);
        assert_eq!(
            k.handle(&envelope("node-a", "set-1", 1)).unwrap().fanout,
            vec!["node-b", "node-c"]
        );
        assert_eq!(
            k.handle(&envelope("node-b", "set-1", 2)).unwrap().fanout,
            vec!["node-c", "node-d"]
        );
        let _ = k.drain_inbound();
    }

    #[test]
    fn two_kernels_fed_the_same_inputs_replay_to_the_same_digest() {
        let inputs = vec![
            envelope("node-a", "set-1", 1),
            envelope("node-b", "set-1", 2),
            envelope("node-a", "set-1", 1), // duplicate
            envelope("node-z", "set-1", 3), // unknown peer
            envelope("node-c", "set-1", 4),
        ];
        let mut a = kernel(8, 8, 2);
        let mut b = kernel(8, 8, 2);
        for e in &inputs {
            let _ = a.handle(e);
            let _ = b.handle(e);
        }
        let replay_a = a.trace().replay();
        let replay_b = b.trace().replay();
        assert_eq!(replay_a, replay_b);
        assert_eq!(replay_a.digest, replay_b.digest);
        assert_eq!(replay_a.accepted_ids.len(), 3);
        assert_eq!(replay_a.refused.len(), 2);
    }

    #[test]
    fn kernel_construction_refuses_a_zero_dedup_capacity_and_a_foreign_self() {
        let bad_capacity = GossipKernel::new(KernelConfig {
            self_id: peer("node-a"),
            validators: set(),
            limits: limits(),
            dedup_capacity: 0,
            queue_capacity: 1,
            fanout: 1,
        })
        .unwrap_err();
        assert_eq!(bad_capacity, KernelError::ZeroDedupCapacity);

        let bad_self = GossipKernel::new(KernelConfig {
            self_id: peer("node-z"),
            validators: set(),
            limits: limits(),
            dedup_capacity: 1,
            queue_capacity: 1,
            fanout: 1,
        })
        .unwrap_err();
        assert_eq!(bad_self, KernelError::SelfNotInSet);
    }
}
