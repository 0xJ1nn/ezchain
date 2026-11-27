//! Bounded, ordered validator (peer) set for the gossip protocol kernel.
//!
//! Oracle: ADR-0051 §"Validator and deployment model" (`docs/DECISIONS.md`). The validator set is
//! an ordered, operator-provisioned list of opaque node identifiers plus a set version that travels
//! on every message. Two deployment shapes are in scope — one operator running all nodes, and a
//! fixed consortium of known operators — but this kernel does not choose between them: the concrete
//! set, its size and any quorum threshold are caller parameters. ADR-0051 is **Proposed** and
//! explicitly decides no concrete number; nothing here hardcodes one (CONTRACT I7).
//!
//! This is pure data. There are no sockets, no keys and no signatures: an opaque id is a label,
//! not an authenticated identity (node authentication is a separate ADR).

use std::collections::BTreeSet;

/// An opaque validator/node identifier (ADR-0051). No structure or meaning is assumed.
pub type PeerId = String;

/// Typed reasons a validator set could not be built. Checked in this order by [`ValidatorSet::new`]
/// so the reported reason is reproducible (ADR-0051 §"Message contract" uses the same discipline).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SetError {
    EmptyVersion,
    EmptyPeer,
    DuplicatePeer,
    TooManyPeers { max: usize },
}

/// An ordered, bounded set of validator ids together with the version label carried on every
/// envelope (ADR-0051). Peers are stored in sorted order so fanout selection is deterministic on
/// every node, independent of the order the operator happened to list them in.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ValidatorSet {
    version: String,
    peers: Vec<PeerId>,
}

impl ValidatorSet {
    /// Build a set from an operator-supplied version and peer list, refusing a version or peer id
    /// that is empty, a duplicate id, or a set larger than `max_size`.
    pub fn new(
        version: impl Into<String>,
        peers: impl IntoIterator<Item = PeerId>,
        max_size: usize,
    ) -> Result<Self, SetError> {
        let version = version.into();
        if version.is_empty() {
            return Err(SetError::EmptyVersion);
        }
        let mut ordered: Vec<PeerId> = peers.into_iter().collect();
        let mut unique = BTreeSet::new();
        for peer in &ordered {
            if peer.is_empty() {
                return Err(SetError::EmptyPeer);
            }
            if !unique.insert(peer.clone()) {
                return Err(SetError::DuplicatePeer);
            }
        }
        if ordered.len() > max_size {
            return Err(SetError::TooManyPeers { max: max_size });
        }
        ordered.sort();
        Ok(Self {
            version,
            peers: ordered,
        })
    }

    /// The version label carried on messages and tape events (ADR-0051).
    pub fn version(&self) -> &str {
        &self.version
    }

    /// The peers in canonical (sorted) order.
    pub fn peers(&self) -> &[PeerId] {
        &self.peers
    }

    /// Whether `peer` is a member of this set. Drives the unknown-peer refusal in the kernel.
    pub fn contains(&self, peer: &str) -> bool {
        self.peers.iter().any(|p| p == peer)
    }

    pub fn len(&self) -> usize {
        self.peers.len()
    }

    pub fn is_empty(&self) -> bool {
        self.peers.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::{PeerId, SetError, ValidatorSet};

    fn ids(items: &[&str]) -> Vec<PeerId> {
        items.iter().map(|s| (*s).to_string()).collect()
    }

    #[test]
    fn a_valid_set_is_ordered_and_reports_membership() {
        let set = ValidatorSet::new("set-1", ids(&["node-c", "node-a", "node-b"]), 3).unwrap();
        assert_eq!(set.version(), "set-1");
        assert_eq!(set.peers(), &["node-a", "node-b", "node-c"]);
        assert_eq!(set.len(), 3);
        assert!(!set.is_empty());
        assert!(set.contains("node-b"));
        assert!(!set.contains("node-z"));
    }

    #[test]
    fn a_single_operator_shape_is_just_a_one_member_set() {
        let set = ValidatorSet::new("solo", ids(&["solo-node"]), 1).unwrap();
        assert_eq!(set.len(), 1);
        assert!(set.contains("solo-node"));
    }

    #[test]
    fn every_set_construction_refusal_has_its_typed_reason() {
        assert_eq!(
            ValidatorSet::new("", ids(&["node-a"]), 3).unwrap_err(),
            SetError::EmptyVersion
        );
        assert_eq!(
            ValidatorSet::new("set-1", ids(&[""]), 3).unwrap_err(),
            SetError::EmptyPeer
        );
        assert_eq!(
            ValidatorSet::new("set-1", ids(&["node-a", "node-a"]), 3).unwrap_err(),
            SetError::DuplicatePeer
        );
        assert_eq!(
            ValidatorSet::new("set-1", ids(&["a", "b"]), 1).unwrap_err(),
            SetError::TooManyPeers { max: 1 }
        );
    }
}
