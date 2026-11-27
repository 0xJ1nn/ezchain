//! Bounded inbound queue with a typed overflow refusal for the gossip protocol kernel.
//!
//! Oracle: ADR-0051 §"Lifecycle" (`docs/DECISIONS.md`) — `Received` is resolved by validation and
//! admission, and CONTRACT I11 forbids unbounded queues. A full queue returns a typed
//! [`QueueRejection::Full`] and changes nothing; it is never silently dropped nor treated as an
//! empty command (fail closed, CONTRACT I3). The queue is generic and pure: it performs no I/O.

use std::collections::VecDeque;

/// Typed refusal from an inbound queue. `Full` carries the capacity that was exceeded so a
/// refusal is replayable from the trace (CONTRACT I9).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum QueueRejection {
    Full { capacity: usize },
}

/// A first-in-first-out queue with a fixed caller-supplied capacity.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InboundQueue<T> {
    capacity: usize,
    items: VecDeque<T>,
}

impl<T> InboundQueue<T> {
    /// Build a queue. A zero capacity is allowed and means "admit nothing" (fail closed).
    pub fn new(capacity: usize) -> Self {
        Self {
            capacity,
            items: VecDeque::with_capacity(capacity),
        }
    }

    /// Admit `item`, or refuse with [`QueueRejection::Full`] without changing the queue.
    pub fn try_push(&mut self, item: T) -> Result<(), QueueRejection> {
        if self.items.len() == self.capacity {
            return Err(QueueRejection::Full {
                capacity: self.capacity,
            });
        }
        self.items.push_back(item);
        Ok(())
    }

    pub fn pop(&mut self) -> Option<T> {
        self.items.pop_front()
    }

    pub fn len(&self) -> usize {
        self.items.len()
    }

    pub fn is_empty(&self) -> bool {
        self.items.is_empty()
    }

    pub fn capacity(&self) -> usize {
        self.capacity
    }
}

#[cfg(test)]
mod tests {
    use super::{InboundQueue, QueueRejection};

    #[test]
    fn items_are_delivered_fifo() {
        let mut q = InboundQueue::new(3);
        assert!(q.is_empty());
        assert_eq!(q.capacity(), 3);
        q.try_push(1).unwrap();
        q.try_push(2).unwrap();
        q.try_push(3).unwrap();
        assert_eq!(q.len(), 3);
        assert_eq!(q.pop(), Some(1));
        assert_eq!(q.pop(), Some(2));
        assert_eq!(q.pop(), Some(3));
        assert_eq!(q.pop(), None);
        assert!(q.is_empty());
    }

    #[test]
    fn overflow_is_a_typed_refusal_that_changes_nothing() {
        let mut q = InboundQueue::new(2);
        q.try_push(10).unwrap();
        q.try_push(20).unwrap();
        assert_eq!(q.try_push(30), Err(QueueRejection::Full { capacity: 2 }));
        assert_eq!(q.len(), 2);
        assert_eq!(q.pop(), Some(10));
        // A freed slot admits a new item again.
        q.try_push(30).unwrap();
        assert_eq!(q.pop(), Some(20));
        assert_eq!(q.pop(), Some(30));
    }

    #[test]
    fn a_zero_capacity_queue_admits_nothing() {
        let mut q = InboundQueue::new(0);
        assert_eq!(q.try_push("x"), Err(QueueRejection::Full { capacity: 0 }));
        assert!(q.is_empty());
    }
}
