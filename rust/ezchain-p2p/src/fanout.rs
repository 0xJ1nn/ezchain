//! Deterministic fanout selection for the gossip protocol kernel.
//!
//! Oracle: ADR-0051 §"Fork and failure handling" and §"Validator and deployment model"
//! (`docs/DECISIONS.md`). ADR-0051 designs gossip but explicitly builds no gossip protocol; this
//! module is the one pure selection rule the kernel needs. It is deliberately deterministic —
//! no RNG, no clock, no peer addresses — so every node selects the same targets from the same
//! inputs. The fanout count and the rotating cursor are caller parameters (CONTRACT I7; ADR-0051
//! leaves all concrete numbers to the maintainer).
//!
//! The rule: exclude the sender (do not gossip back to the peer that sent the message), sort and
//! de-duplicate the candidates, then take a window of at most `fanout` peers starting at
//! `cursor % candidates.len()`, wrapping around. A fresh cursor each round spreads delivery
//! across the set without randomness.

use crate::peer::PeerId;

/// Select up to `fanout` peers to forward to, excluding `sender`.
pub fn select_fanout(peers: &[PeerId], sender: &PeerId, fanout: usize, cursor: u64) -> Vec<PeerId> {
    if fanout == 0 {
        return Vec::new();
    }
    let mut candidates: Vec<PeerId> = peers.iter().filter(|p| *p != sender).cloned().collect();
    candidates.sort();
    candidates.dedup();
    if candidates.is_empty() {
        return Vec::new();
    }
    let count = fanout.min(candidates.len());
    let start = (cursor % candidates.len() as u64) as usize;
    (0..count)
        .map(|i| candidates[(start + i) % candidates.len()].clone())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::select_fanout;

    fn peers() -> Vec<String> {
        ["node-a", "node-b", "node-c", "node-d"]
            .iter()
            .map(|s| (*s).to_string())
            .collect()
    }

    #[test]
    fn selection_excludes_the_sender_is_sorted_and_is_deterministic() {
        let sender = "node-c".to_string();
        let a = select_fanout(&peers(), &sender, 2, 0);
        let b = select_fanout(&peers(), &sender, 2, 0);
        assert_eq!(a, b);
        assert_eq!(a, vec!["node-a", "node-b"]);
        assert!(!a.contains(&sender));
    }

    #[test]
    fn a_zero_fanout_selects_nobody() {
        assert!(select_fanout(&peers(), &"node-a".to_string(), 0, 0).is_empty());
    }

    #[test]
    fn fanout_is_capped_at_the_available_peer_count() {
        let all = select_fanout(&peers(), &"node-a".to_string(), 99, 0);
        assert_eq!(all.len(), 3);
        assert!(!all.contains(&"node-a".to_string()));
    }

    #[test]
    fn the_cursor_rotates_and_wraps_the_selection_window() {
        let sender = "node-z".to_string(); // not in the set, so all four are candidates
        assert_eq!(
            select_fanout(&peers(), &sender, 2, 0),
            vec!["node-a", "node-b"]
        );
        assert_eq!(
            select_fanout(&peers(), &sender, 2, 1),
            vec!["node-b", "node-c"]
        );
        assert_eq!(
            select_fanout(&peers(), &sender, 2, 3),
            vec!["node-d", "node-a"]
        );
        assert_eq!(
            select_fanout(&peers(), &sender, 2, 4),
            vec!["node-a", "node-b"]
        );
    }

    #[test]
    fn a_set_with_no_eligible_peer_selects_nobody() {
        let only = vec!["node-a".to_string()];
        assert!(select_fanout(&only, &"node-a".to_string(), 3, 7).is_empty());
    }
}
