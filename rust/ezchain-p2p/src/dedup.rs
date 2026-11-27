//! Bounded deduplication of envelope content ids for the gossip protocol kernel.
//!
//! Oracle: ADR-0051 §"Message contract" (`docs/DECISIONS.md`). The id used here is the
//! deterministic content id returned by [`crate::envelope::validate`] (FNV-1a over canonical
//! bytes). It is identity within a trusted operator group, not a signature. This is a "recently
//! seen" cache: it is bounded (CONTRACT I11) and evicts the oldest id on a first-in-first-out rule
//! when full, so memory cannot grow without limit. An id evicted after `capacity` distinct later
//! messages may be offered again; that is the documented cost of a bounded deduplicator.

use std::collections::{BTreeSet, VecDeque};

/// Refusal from constructing a deduplicator. A zero capacity is refused rather than silently
/// turning every message into a duplicate (fail closed, CONTRACT I3).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CapacityError {
    Zero,
}

/// A bounded FIFO set of recently seen envelope content ids.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SeenIds {
    capacity: usize,
    order: VecDeque<u64>,
    seen: BTreeSet<u64>,
}

impl SeenIds {
    pub fn new(capacity: usize) -> Result<Self, CapacityError> {
        if capacity == 0 {
            return Err(CapacityError::Zero);
        }
        Ok(Self {
            capacity,
            order: VecDeque::with_capacity(capacity),
            seen: BTreeSet::new(),
        })
    }

    /// Record `id` if it has not been seen recently. Returns `true` when it was newly recorded
    /// and `false` when it is a duplicate. Insertion order is tracked so eviction is deterministic.
    pub fn offer(&mut self, id: u64) -> bool {
        if self.seen.contains(&id) {
            return false;
        }
        if self.order.len() == self.capacity {
            if let Some(oldest) = self.order.pop_front() {
                self.seen.remove(&oldest);
            }
        }
        self.order.push_back(id);
        self.seen.insert(id);
        true
    }

    pub fn contains(&self, id: u64) -> bool {
        self.seen.contains(&id)
    }

    pub fn len(&self) -> usize {
        self.seen.len()
    }

    pub fn is_empty(&self) -> bool {
        self.seen.is_empty()
    }

    pub fn capacity(&self) -> usize {
        self.capacity
    }
}

#[cfg(test)]
mod tests {
    use super::{CapacityError, SeenIds};

    #[test]
    fn a_new_id_is_recorded_and_a_second_offer_is_a_duplicate() {
        let mut seen = SeenIds::new(4).unwrap();
        assert!(seen.is_empty());
        assert!(seen.offer(42));
        assert!(seen.contains(42));
        assert_eq!(seen.len(), 1);
        assert!(!seen.offer(42));
        assert_eq!(seen.len(), 1);
        assert!(seen.offer(43));
        assert_eq!(seen.len(), 2);
    }

    #[test]
    fn a_full_cache_evicts_the_oldest_id_deterministically() {
        let mut seen = SeenIds::new(2).unwrap();
        assert!(seen.offer(1));
        assert!(seen.offer(2));
        assert_eq!(seen.len(), 2);
        assert!(seen.offer(3));
        assert_eq!(seen.len(), 2);
        assert!(!seen.contains(1));
        assert!(seen.contains(2));
        assert!(seen.contains(3));
        // A duplicate of the surviving id is still suppressed; the evicted id can be offered.
        assert!(!seen.offer(2));
        assert!(seen.offer(1));
    }

    #[test]
    fn zero_capacity_is_refused() {
        assert_eq!(SeenIds::new(0).unwrap_err(), CapacityError::Zero);
    }
}
