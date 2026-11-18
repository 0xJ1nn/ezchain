//! Pure quorum/certificate validation over a fixed permissioned validator set.
//!
//! Oracle: ADR-0051 §§ "Validator and deployment model", "Fork and failure handling" and
//! "Live money and keys remain owner-blocked" in `docs/DECISIONS.md`. ADR-0051 is still
//! **Proposed**; the validator model, set-size/quorum parameters and fork-choice rule in this
//! module implement its recommendations, not an accepted protocol decision.
//!
//! This is a pure kernel. Set size and threshold are caller-supplied parameters (CONTRACT I7),
//! votes carry an opaque token that is never verified, and no message is ever sent. A certificate
//! is produced only by [`VoteBook`] after duplicate and equivocation checks, and a recorded tape
//! is committed only when a replay of its prefix matches that certificate. Fork choice applies
//! the ADR's rule (lowest head digest, then lowest set version, then lowest node id) and refuses
//! to commit on unresolved divergence (quarantine).
//!
//! A [`SignatureToken`] is an opaque typed placeholder supplied by the caller, **not** a
//! signature: the kernel holds no keys, produces no signatures and verifies no tokens. Node
//! authentication is a separate ADR (ADR-0051). No money, no ledger writes, no I/O.

use std::collections::BTreeMap;

use crate::tape::{self, Tape};

/// An opaque, non-cryptographic token attached to a vote. A typed stand-in for a future
/// signature; the kernel treats it as inert data and never interprets or verifies it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct SignatureToken(pub u64);

/// Every way a caller can misconfigure a [`ValidatorSet`]. Checked in the order
/// [`ValidatorSet::new`] lists them, so competing faults report the same reason.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConfigError {
    EmptySetVersion,
    EmptyValidatorSet,
    ZeroThreshold,
    ThresholdExceedsSetSize,
    EmptyValidatorId,
    DuplicateValidator,
}

/// A fixed, operator-provisioned set of opaque validator ids bound to one set version.
/// The set size and the quorum threshold are parameters, never literals (ADR-0051, I7);
/// choosing a safe threshold (for example a strict majority) remains an open decision.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ValidatorSet {
    set_version: String,
    validators: Vec<String>,
    threshold: usize,
}

impl ValidatorSet {
    /// Construct a set, validating in this fixed order: non-empty set version, non-empty set,
    /// non-zero threshold, threshold not larger than the set, non-empty ids, no duplicates.
    pub fn new(
        set_version: impl Into<String>,
        validators: Vec<String>,
        threshold: usize,
    ) -> Result<Self, ConfigError> {
        let set_version = set_version.into();
        if set_version.is_empty() {
            return Err(ConfigError::EmptySetVersion);
        }
        if validators.is_empty() {
            return Err(ConfigError::EmptyValidatorSet);
        }
        if threshold == 0 {
            return Err(ConfigError::ZeroThreshold);
        }
        if threshold > validators.len() {
            return Err(ConfigError::ThresholdExceedsSetSize);
        }
        let mut seen = BTreeMap::new();
        for validator in &validators {
            if validator.is_empty() {
                return Err(ConfigError::EmptyValidatorId);
            }
            if seen.insert(validator.as_str(), ()).is_some() {
                return Err(ConfigError::DuplicateValidator);
            }
        }
        Ok(Self {
            set_version,
            validators,
            threshold,
        })
    }

    /// The set version every vote and tape event must name.
    pub fn set_version(&self) -> &str {
        &self.set_version
    }

    /// The ordered, operator-provisioned validator ids.
    pub fn validators(&self) -> &[String] {
        &self.validators
    }

    /// Configured quorum threshold (number of distinct validators required).
    pub fn threshold(&self) -> usize {
        self.threshold
    }

    /// Number of validators in the set.
    pub fn len(&self) -> usize {
        self.validators.len()
    }

    /// A valid set is never empty.
    pub fn is_empty(&self) -> bool {
        false
    }

    /// Whether `voter` is a member of this set.
    pub fn contains(&self, voter: &str) -> bool {
        self.validators.iter().any(|id| id == voter)
    }
}

/// One vote by one validator for a head digest at a tape position. `signature` is an opaque
/// token; the kernel never verifies it (see [`SignatureToken`]).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SignedVote {
    pub voter: String,
    pub set_version: String,
    /// The tape position this vote is cast at.
    pub height: u64,
    /// The head digest being voted for (a `tape::state_digest` of the candidate prefix).
    pub digest: u64,
    /// Opaque identity placeholder, not a cryptographic signature.
    pub signature: SignatureToken,
}

/// One validator emitting two different digests at the same height. Recorded, then used to
/// taint that height so no certificate can be assembled from it (fail closed, I3).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Equivocation {
    pub voter: String,
    pub height: u64,
    pub first_digest: u64,
    pub second_digest: u64,
}

/// Typed reasons a vote is refused. Checked in the order [`VoteBook::add`] lists them.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum VoteError {
    SetVersionMismatch {
        expected: String,
        found: String,
    },
    UnknownValidator {
        voter: String,
    },
    DuplicateVote {
        voter: String,
        height: u64,
        digest: u64,
    },
    Equivocation(Equivocation),
}

/// Typed reasons certificate assembly fails. Both fail closed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CertificateError {
    /// An equivocation was recorded at this height; no certificate may be formed there.
    EquivocationAtHeight { height: u64 },
    /// Two different digests each reached the threshold; the divergence is unresolved.
    ConflictingQuorums { height: u64 },
}

/// A quorum certificate. Fields are private: the only constructor is [`VoteBook::certificate`],
/// so a certificate always represents at least the configured threshold of distinct validators
/// voting for one digest at one height in one set version.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Certificate {
    set_version: String,
    height: u64,
    digest: u64,
    votes: Vec<SignedVote>,
}

impl Certificate {
    pub fn set_version(&self) -> &str {
        &self.set_version
    }

    pub fn height(&self) -> u64 {
        self.height
    }

    pub fn digest(&self) -> u64 {
        self.digest
    }

    /// Votes ordered by validator id, independent of arrival order.
    pub fn votes(&self) -> &[SignedVote] {
        &self.votes
    }
}

/// Accumulates votes for one validator set, rejecting duplicates and recording equivocation.
#[derive(Debug, Clone)]
pub struct VoteBook {
    set: ValidatorSet,
    votes: Vec<SignedVote>,
    equivocations: Vec<Equivocation>,
}

impl VoteBook {
    pub fn new(set: ValidatorSet) -> Self {
        Self {
            set,
            votes: Vec::new(),
            equivocations: Vec::new(),
        }
    }

    pub fn set(&self) -> &ValidatorSet {
        &self.set
    }

    pub fn votes(&self) -> &[SignedVote] {
        &self.votes
    }

    pub fn equivocations(&self) -> &[Equivocation] {
        &self.equivocations
    }

    /// Admit one vote. Checks set version, membership, duplicate, then equivocation. A refused
    /// vote is never recorded and never changes the book (fail closed, I3).
    pub fn add(&mut self, vote: SignedVote) -> Result<(), VoteError> {
        if vote.set_version != self.set.set_version {
            return Err(VoteError::SetVersionMismatch {
                expected: self.set.set_version.clone(),
                found: vote.set_version.clone(),
            });
        }
        if !self.set.contains(&vote.voter) {
            return Err(VoteError::UnknownValidator {
                voter: vote.voter.clone(),
            });
        }
        if let Some(existing) = self
            .votes
            .iter()
            .find(|known| known.voter == vote.voter && known.height == vote.height)
        {
            if existing.digest == vote.digest {
                return Err(VoteError::DuplicateVote {
                    voter: vote.voter.clone(),
                    height: vote.height,
                    digest: vote.digest,
                });
            }
            let equivocation = Equivocation {
                voter: vote.voter.clone(),
                height: vote.height,
                first_digest: existing.digest,
                second_digest: vote.digest,
            };
            self.equivocations.push(equivocation.clone());
            return Err(VoteError::Equivocation(equivocation));
        }
        self.votes.push(vote);
        Ok(())
    }

    /// Assemble a certificate for `height`, or `None` while no digest has threshold support.
    /// A recorded equivocation at the height, or two quorums on different digests, is refused.
    pub fn certificate(&self, height: u64) -> Result<Option<Certificate>, CertificateError> {
        if self.equivocations.iter().any(|eq| eq.height == height) {
            return Err(CertificateError::EquivocationAtHeight { height });
        }
        let mut by_digest: BTreeMap<u64, Vec<&SignedVote>> = BTreeMap::new();
        for vote in self.votes.iter().filter(|vote| vote.height == height) {
            by_digest.entry(vote.digest).or_default().push(vote);
        }
        let mut qualified: Vec<u64> = by_digest
            .iter()
            .filter(|(_, votes)| votes.len() >= self.set.threshold)
            .map(|(digest, _)| *digest)
            .collect();
        match qualified.len() {
            0 => Ok(None),
            1 => {
                let digest = qualified.remove(0);
                let mut votes: Vec<SignedVote> = by_digest[&digest]
                    .iter()
                    .map(|vote| (*vote).clone())
                    .collect();
                votes.sort_by(|left, right| left.voter.cmp(&right.voter));
                Ok(Some(Certificate {
                    set_version: self.set.set_version.clone(),
                    height,
                    digest,
                    votes,
                }))
            }
            _ => Err(CertificateError::ConflictingQuorums { height }),
        }
    }
}

/// The typed result of a successful commit: the prefix the certificate certified.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Commitment {
    pub height: u64,
    pub digest: u64,
    pub set_version: String,
}

/// Typed reasons a certificate cannot commit a recorded tape. [`CommitError::Quarantined`] is
/// produced only by [`commit_guarded`], never by [`commit`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CommitError {
    Quarantined,
    SetVersionMismatch { expected: String, found: String },
    HeightBeyondTape { height: u64, tape_len: u64 },
    DigestMismatch { expected: u64, found: u64 },
    Replay(tape::ReplayError),
}

/// Replay the first `height` events of `recorded` with the [`tape`] kernel and compare the
/// resulting state digest to the certificate. No state is written and no message is sent.
pub fn commit(certificate: &Certificate, recorded: &Tape) -> Result<Commitment, CommitError> {
    if certificate.set_version() != recorded.set_version {
        return Err(CommitError::SetVersionMismatch {
            expected: recorded.set_version.clone(),
            found: certificate.set_version().to_string(),
        });
    }
    let tape_len = recorded.events.len() as u64;
    let height = match usize::try_from(certificate.height()) {
        Ok(height) => height,
        Err(_) => {
            return Err(CommitError::HeightBeyondTape {
                height: certificate.height(),
                tape_len,
            })
        }
    };
    if height > recorded.events.len() {
        return Err(CommitError::HeightBeyondTape {
            height: certificate.height(),
            tape_len,
        });
    }
    let prefix = Tape {
        set_version: recorded.set_version.clone(),
        events: recorded.events[..height].to_vec(),
    };
    let state = tape::replay(&prefix).map_err(CommitError::Replay)?;
    let digest = tape::state_digest(&state);
    if digest != certificate.digest() {
        return Err(CommitError::DigestMismatch {
            expected: certificate.digest(),
            found: digest,
        });
    }
    Ok(Commitment {
        height: certificate.height(),
        digest,
        set_version: recorded.set_version.clone(),
    })
}

/// A node holding the head it knows, reduced to what fork choice compares. Derived from a tape
/// by replaying it, so the head digest is the deterministic projection of accepted events.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TapeHead {
    pub node_id: String,
    pub set_version: String,
    pub head_digest: u64,
    pub length: u64,
    pub last_applied_id: Option<u64>,
}

impl TapeHead {
    /// Derive a head from a tape with the replay kernel (ADR-0051 §"State contract and replay").
    pub fn from_tape(node_id: impl Into<String>, tape: &Tape) -> Result<Self, tape::ReplayError> {
        let state = tape::replay(tape)?;
        Ok(Self {
            node_id: node_id.into(),
            set_version: tape.set_version.clone(),
            head_digest: tape::state_digest(&state),
            length: tape.events.len() as u64,
            last_applied_id: state.applied.last().copied(),
        })
    }

    /// The fork-choice key: lowest head digest, then lowest set version, then lowest node id.
    fn choice_key(&self) -> (u64, &str, &str) {
        (
            self.head_digest,
            self.set_version.as_str(),
            self.node_id.as_str(),
        )
    }
}

/// Outcome of fork choice over the heads known to a node. A quarantine carries the conflicting
/// heads for the operator to resolve in a new set version (ADR-0051 §"Fork and failure handling").
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ForkDecision<'a> {
    /// All known heads agree; no resolution is needed.
    Unanimous(&'a TapeHead),
    /// Divergence was resolved by the deterministic rule; this head won.
    Resolved(&'a TapeHead),
    /// Divergence with identical choice keys cannot be resolved; the node quarantines.
    Quarantined(Vec<TapeHead>),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ForkChoiceError {
    NoCandidates,
    Replay(tape::ReplayError),
}

/// Compare the heads a node knows. Divergence is detected by head digest, tape length, last
/// applied id and set version; resolution is lowest head digest, then lowest set version, then
/// lowest node id. If that key cannot separate divergent heads, quarantine (fail closed).
pub fn decide_fork(candidates: &[TapeHead]) -> Result<ForkDecision<'_>, ForkChoiceError> {
    let first = candidates.first().ok_or(ForkChoiceError::NoCandidates)?;
    let divergent = candidates.iter().skip(1).any(|head| {
        head.head_digest != first.head_digest
            || head.set_version != first.set_version
            || head.length != first.length
            || head.last_applied_id != first.last_applied_id
    });
    if !divergent {
        return Ok(ForkDecision::Unanimous(first));
    }
    let mut best = first;
    for head in &candidates[1..] {
        if head.choice_key() < best.choice_key() {
            best = head;
        }
    }
    let tied = candidates
        .iter()
        .filter(|head| head.choice_key() == best.choice_key())
        .count();
    if tied == 1 {
        Ok(ForkDecision::Resolved(best))
    } else {
        Ok(ForkDecision::Quarantined(candidates.to_vec()))
    }
}

/// Commit only when fork choice did not quarantine this node. A quarantined node stops
/// advancing state and refuses further events until an operator publishes a new set version.
pub fn commit_guarded(
    certificate: &Certificate,
    recorded: &Tape,
    decision: &ForkDecision<'_>,
) -> Result<Commitment, CommitError> {
    if matches!(decision, ForkDecision::Quarantined(_)) {
        return Err(CommitError::Quarantined);
    }
    commit(certificate, recorded)
}

#[cfg(test)]
mod tests {
    use super::{
        commit, commit_guarded, decide_fork, Certificate, CertificateError, CommitError,
        Commitment, ConfigError, Equivocation, ForkChoiceError, ForkDecision, SignatureToken,
        SignedVote, TapeHead, ValidatorSet, VoteBook, VoteError,
    };
    use crate::tape::{self, Command, ReplayError, Tape, TapeEvent};

    // Oracle: ADR-0051 §§ "Validator and deployment model", "Fork and failure handling" and
    // "Live money and keys remain owner-blocked" (docs/DECISIONS.md). ADR-0051 is still Proposed;
    // these tests record its written contract, not a behaviour copied from a Python original.

    fn set(validators: &[&str], threshold: usize) -> ValidatorSet {
        ValidatorSet::new(
            "set-1",
            validators.iter().map(|id| (*id).to_string()).collect(),
            threshold,
        )
        .expect("valid set")
    }

    fn vote(voter: &str, height: u64, digest: u64) -> SignedVote {
        SignedVote {
            voter: voter.to_string(),
            set_version: "set-1".to_string(),
            height,
            digest,
            signature: SignatureToken(7),
        }
    }

    fn tape_n_events(n: u64) -> Tape {
        Tape {
            set_version: "set-1".to_string(),
            events: (1..=n)
                .map(|i| TapeEvent {
                    id: i,
                    sender: format!("node-{i}"),
                    set_version: "set-1".to_string(),
                    seq: 1,
                    command: Command::Set {
                        key: format!("key-{i}"),
                        value: format!("value-{i}"),
                    },
                })
                .collect(),
        }
    }

    fn tape_digest(tape: &Tape) -> u64 {
        tape::state_digest(&tape::replay(tape).expect("replay"))
    }

    fn certificate_with_digest(
        validators: &[&str],
        threshold: usize,
        height: u64,
        digest: u64,
    ) -> Certificate {
        let mut book = VoteBook::new(set(validators, threshold));
        for id in validators.iter().take(threshold) {
            book.add(vote(id, height, digest)).expect("vote");
        }
        book.certificate(height)
            .expect("certificate")
            .expect("some")
    }

    fn certificate_for_prefix(
        tape: &Tape,
        height: usize,
        validators: &[&str],
        threshold: usize,
    ) -> Certificate {
        let prefix = Tape {
            set_version: tape.set_version.clone(),
            events: tape.events[..height].to_vec(),
        };
        certificate_with_digest(validators, threshold, height as u64, tape_digest(&prefix))
    }

    fn head(
        node_id: &str,
        set_version: &str,
        digest: u64,
        length: u64,
        last_applied_id: Option<u64>,
    ) -> TapeHead {
        TapeHead {
            node_id: node_id.to_string(),
            set_version: set_version.to_string(),
            head_digest: digest,
            length,
            last_applied_id,
        }
    }

    // --- configuration ---

    #[test]
    fn a_valid_set_reports_its_parameters() {
        let validators = set(&["node-a", "node-b", "node-c"], 2);
        assert_eq!(validators.set_version(), "set-1");
        assert_eq!(
            validators.validators(),
            &[
                "node-a".to_string(),
                "node-b".to_string(),
                "node-c".to_string()
            ]
        );
        assert_eq!(validators.len(), 3);
        assert!(!validators.is_empty());
        assert_eq!(validators.threshold(), 2);
        assert!(validators.contains("node-a"));
        assert!(!validators.contains("node-d"));
    }

    #[test]
    fn every_invalid_configuration_has_its_typed_reason() {
        assert_eq!(
            ValidatorSet::new("", vec!["node-a".to_string()], 1),
            Err(ConfigError::EmptySetVersion)
        );
        assert_eq!(
            ValidatorSet::new("set-1", Vec::new(), 1),
            Err(ConfigError::EmptyValidatorSet)
        );
        assert_eq!(
            ValidatorSet::new("set-1", vec!["node-a".to_string()], 0),
            Err(ConfigError::ZeroThreshold)
        );
        assert_eq!(
            ValidatorSet::new("set-1", vec!["node-a".to_string()], 2),
            Err(ConfigError::ThresholdExceedsSetSize)
        );
        assert_eq!(
            ValidatorSet::new("set-1", vec![String::new()], 1),
            Err(ConfigError::EmptyValidatorId)
        );
        assert_eq!(
            ValidatorSet::new("set-1", vec!["node-a".to_string(), "node-a".to_string()], 1),
            Err(ConfigError::DuplicateValidator)
        );
    }

    // --- vote admission ---

    #[test]
    fn a_valid_vote_is_recorded() {
        let mut book = VoteBook::new(set(&["node-a"], 1));
        assert_eq!(book.set().threshold(), 1);
        assert!(book.votes().is_empty());
        assert!(book.equivocations().is_empty());
        book.add(vote("node-a", 1, 42)).expect("vote");
        assert_eq!(book.votes().len(), 1);
        assert_eq!(book.votes()[0].digest, 42);
        assert_eq!(book.votes()[0].signature, SignatureToken(7));
    }

    #[test]
    fn a_vote_from_another_set_version_is_refused() {
        let mut book = VoteBook::new(set(&["node-a"], 1));
        let mut foreign = vote("node-a", 1, 42);
        foreign.set_version = "set-2".to_string();
        assert_eq!(
            book.add(foreign),
            Err(VoteError::SetVersionMismatch {
                expected: "set-1".to_string(),
                found: "set-2".to_string()
            })
        );
        assert!(book.votes().is_empty());
    }

    #[test]
    fn a_vote_from_an_unknown_validator_is_refused() {
        let mut book = VoteBook::new(set(&["node-a"], 1));
        assert_eq!(
            book.add(vote("node-x", 1, 42)),
            Err(VoteError::UnknownValidator {
                voter: "node-x".to_string()
            })
        );
        assert!(book.votes().is_empty());
    }

    #[test]
    fn set_version_is_checked_before_membership() {
        let mut book = VoteBook::new(set(&["node-a"], 1));
        let mut foreign = vote("node-x", 1, 42);
        foreign.set_version = "set-2".to_string();
        assert_eq!(
            book.add(foreign),
            Err(VoteError::SetVersionMismatch {
                expected: "set-1".to_string(),
                found: "set-2".to_string()
            })
        );
    }

    #[test]
    fn a_duplicate_vote_is_refused_even_with_a_different_token() {
        let mut book = VoteBook::new(set(&["node-a"], 1));
        book.add(vote("node-a", 1, 42)).expect("first vote");
        let mut duplicate = vote("node-a", 1, 42);
        duplicate.signature = SignatureToken(999);
        assert_eq!(
            book.add(duplicate),
            Err(VoteError::DuplicateVote {
                voter: "node-a".to_string(),
                height: 1,
                digest: 42
            })
        );
        assert_eq!(book.votes().len(), 1);
    }

    #[test]
    fn a_second_digest_from_one_validator_is_equivocation() {
        let mut book = VoteBook::new(set(&["node-a", "node-b"], 2));
        book.add(vote("node-a", 3, 10)).expect("first vote");
        let expected = Equivocation {
            voter: "node-a".to_string(),
            height: 3,
            first_digest: 10,
            second_digest: 20,
        };
        assert_eq!(
            book.add(vote("node-a", 3, 20)),
            Err(VoteError::Equivocation(expected.clone()))
        );
        assert_eq!(book.votes().len(), 1);
        assert_eq!(book.equivocations(), &[expected]);
    }

    #[test]
    fn votes_at_different_heights_by_one_validator_are_not_equivocation() {
        let mut book = VoteBook::new(set(&["node-a"], 1));
        book.add(vote("node-a", 1, 10)).expect("height 1");
        book.add(vote("node-a", 2, 20)).expect("height 2");
        assert_eq!(book.votes().len(), 2);
        assert!(book.equivocations().is_empty());
    }

    #[test]
    fn an_opaque_token_is_never_verified() {
        let mut book = VoteBook::new(set(&["node-a", "node-b"], 2));
        let mut zero = vote("node-a", 1, 5);
        zero.signature = SignatureToken(0);
        let mut arbitrary = vote("node-b", 1, 5);
        arbitrary.signature = SignatureToken(u64::MAX);
        book.add(zero).expect("zero token");
        book.add(arbitrary).expect("arbitrary token");
        assert!(book.certificate(1).expect("certificate").is_some());
    }

    // --- certificate assembly ---

    #[test]
    fn below_threshold_there_is_no_certificate() {
        let mut book = VoteBook::new(set(&["node-a", "node-b", "node-c"], 2));
        book.add(vote("node-a", 1, 42)).expect("vote");
        assert_eq!(book.certificate(1), Ok(None));
        assert_eq!(book.certificate(2), Ok(None));
    }

    #[test]
    fn exactly_threshold_assembles_a_certificate() {
        let mut book = VoteBook::new(set(&["node-a", "node-b", "node-c"], 2));
        book.add(vote("node-b", 4, 42)).expect("node-b");
        book.add(vote("node-a", 4, 42)).expect("node-a");
        let certificate = book.certificate(4).expect("certificate").expect("some");
        assert_eq!(certificate.set_version(), "set-1");
        assert_eq!(certificate.height(), 4);
        assert_eq!(certificate.digest(), 42);
        assert_eq!(certificate.votes().len(), 2);
        assert_eq!(certificate.votes()[0].voter, "node-a");
        assert_eq!(certificate.votes()[1].voter, "node-b");
    }

    #[test]
    fn a_certificate_requires_distinct_validators() {
        let mut book = VoteBook::new(set(&["node-a", "node-b"], 2));
        book.add(vote("node-a", 1, 9)).expect("node-a");
        assert_eq!(
            book.add(vote("node-a", 1, 9)),
            Err(VoteError::DuplicateVote {
                voter: "node-a".to_string(),
                height: 1,
                digest: 9
            })
        );
        assert_eq!(book.votes().len(), 1);
        assert_eq!(book.certificate(1), Ok(None));
    }

    #[test]
    fn certificate_votes_are_deterministic_across_arrival_order() {
        let forward = certificate_with_digest(&["node-a", "node-b", "node-c"], 3, 2, 77);
        let mut reverse_book = VoteBook::new(set(&["node-a", "node-b", "node-c"], 3));
        for id in ["node-c", "node-b", "node-a"] {
            reverse_book.add(vote(id, 2, 77)).expect("vote");
        }
        let reverse = reverse_book
            .certificate(2)
            .expect("certificate")
            .expect("some");
        assert_eq!(forward, reverse);
        assert_eq!(
            forward
                .votes()
                .iter()
                .map(|v| v.voter.as_str())
                .collect::<Vec<_>>(),
            vec!["node-a", "node-b", "node-c"]
        );
    }

    #[test]
    fn split_votes_below_threshold_do_not_certify() {
        let mut book = VoteBook::new(set(&["node-a", "node-b", "node-c"], 3));
        book.add(vote("node-a", 1, 10)).expect("node-a");
        book.add(vote("node-b", 1, 20)).expect("node-b");
        assert_eq!(book.certificate(1), Ok(None));
    }

    #[test]
    fn equivocation_taints_only_its_own_height() {
        let mut book = VoteBook::new(set(&["node-a", "node-b"], 2));
        book.add(vote("node-a", 1, 10)).expect("node-a at 1");
        book.add(vote("node-a", 1, 20)).expect_err("equivocation");
        book.add(vote("node-a", 2, 30)).expect("node-a at 2");
        book.add(vote("node-b", 2, 30)).expect("node-b at 2");
        assert_eq!(
            book.certificate(1),
            Err(CertificateError::EquivocationAtHeight { height: 1 })
        );
        assert!(book.certificate(2).expect("certificate 2").is_some());
    }

    #[test]
    fn two_quorums_on_different_digests_are_refused() {
        let mut book = VoteBook::new(set(&["node-a", "node-b", "node-c", "node-d"], 2));
        book.add(vote("node-a", 5, 10)).expect("node-a");
        book.add(vote("node-b", 5, 10)).expect("node-b");
        book.add(vote("node-c", 5, 20)).expect("node-c");
        book.add(vote("node-d", 5, 20)).expect("node-d");
        assert_eq!(
            book.certificate(5),
            Err(CertificateError::ConflictingQuorums { height: 5 })
        );
    }

    // --- commit rule, reusing the replay kernel ---

    #[test]
    fn a_certificate_commits_a_matching_tape() {
        let tape = tape_n_events(2);
        let certificate = certificate_for_prefix(&tape, 2, &["node-a", "node-b", "node-c"], 2);
        let commitment = commit(&certificate, &tape).expect("commit");
        assert_eq!(
            commitment,
            Commitment {
                height: 2,
                digest: tape_digest(&tape),
                set_version: "set-1".to_string()
            }
        );
    }

    #[test]
    fn a_prefix_certificate_commits_only_the_recorded_prefix() {
        let tape = tape_n_events(3);
        let certificate = certificate_for_prefix(&tape, 1, &["node-a", "node-b"], 2);
        assert_ne!(certificate.digest(), tape_digest(&tape));
        let commitment = commit(&certificate, &tape).expect("commit prefix");
        assert_eq!(commitment.height, 1);
        assert_eq!(commitment.digest, certificate.digest());
    }

    #[test]
    fn a_wrong_digest_is_refused_without_committing() {
        let tape = tape_n_events(2);
        let wrong = tape_digest(&tape) ^ 1;
        let certificate = certificate_with_digest(&["node-a", "node-b"], 2, 2, wrong);
        assert_eq!(
            commit(&certificate, &tape),
            Err(CommitError::DigestMismatch {
                expected: wrong,
                found: tape_digest(&tape)
            })
        );
    }

    #[test]
    fn a_height_beyond_the_tape_is_refused() {
        let tape = tape_n_events(2);
        let certificate = certificate_with_digest(&["node-a", "node-b"], 2, 9, 123);
        assert_eq!(
            commit(&certificate, &tape),
            Err(CommitError::HeightBeyondTape {
                height: 9,
                tape_len: 2
            })
        );
    }

    #[test]
    fn a_tape_from_another_set_version_is_refused() {
        let mut tape = tape_n_events(1);
        tape.set_version = "set-2".to_string();
        let certificate = certificate_with_digest(&["node-a", "node-b"], 2, 1, 99);
        assert_eq!(
            commit(&certificate, &tape),
            Err(CommitError::SetVersionMismatch {
                expected: "set-2".to_string(),
                found: "set-1".to_string()
            })
        );
    }

    #[test]
    fn an_invalid_tape_propagates_the_replay_error() {
        let mut tape = tape_n_events(2);
        tape.events[1].seq = 0;
        // Commit at height 2 so the replay kernel actually sees the invalid event.
        let certificate = certificate_with_digest(&["node-a"], 1, 2, 123);
        assert_eq!(
            commit(&certificate, &tape),
            Err(CommitError::Replay(ReplayError::SequenceNotMonotonic))
        );
    }

    #[test]
    fn an_empty_prefix_commits_with_the_empty_state_digest() {
        let tape = tape_n_events(2);
        let empty = Tape {
            set_version: "set-1".to_string(),
            events: Vec::new(),
        };
        let digest = tape_digest(&empty);
        let certificate = certificate_with_digest(&["node-a"], 1, 0, digest);
        assert_eq!(
            commit(&certificate, &tape),
            Ok(Commitment {
                height: 0,
                digest,
                set_version: "set-1".to_string()
            })
        );
    }

    // --- fork choice and quarantine ---

    #[test]
    fn a_head_is_derived_from_the_replay_kernel() {
        let tape = tape_n_events(2);
        let head = TapeHead::from_tape("node-a", &tape).expect("head");
        assert_eq!(head.node_id, "node-a");
        assert_eq!(head.set_version, "set-1");
        assert_eq!(head.head_digest, tape_digest(&tape));
        assert_eq!(head.length, 2);
        assert_eq!(head.last_applied_id, Some(2));
    }

    #[test]
    fn replay_errors_are_visible_when_deriving_a_head() {
        let mut tape = tape_n_events(1);
        tape.events[0].seq = 0;
        assert_eq!(
            TapeHead::from_tape("node-a", &tape),
            Err(ReplayError::SequenceNotMonotonic)
        );
    }

    #[test]
    fn no_candidates_is_a_typed_error() {
        assert_eq!(decide_fork(&[]), Err(ForkChoiceError::NoCandidates));
    }

    #[test]
    fn heads_that_agree_need_no_resolution() {
        let tape = tape_n_events(2);
        let a = TapeHead::from_tape("node-a", &tape).expect("head a");
        let b = TapeHead::from_tape("node-b", &tape).expect("head b");
        match decide_fork(&[a.clone(), b]).expect("decision") {
            ForkDecision::Unanimous(winner) => assert_eq!(winner, &a),
            other => panic!("expected unanimous, got {other:?}"),
        }
    }

    #[test]
    fn the_lowest_head_digest_wins_a_divergence() {
        // ADR-0051 has no longest-tape rule: the lowest digest wins even against a longer tape.
        let high = head("node-a", "set-1", 900, 3, Some(3));
        let low = head("node-b", "set-1", 100, 2, Some(2));
        match decide_fork(&[high, low.clone()]).expect("decision") {
            ForkDecision::Resolved(winner) => assert_eq!(winner, &low),
            other => panic!("expected resolved, got {other:?}"),
        }
    }

    #[test]
    fn set_version_breaks_a_digest_tie() {
        let later = head("node-a", "set-2", 55, 2, Some(2));
        let earlier = head("node-z", "set-1", 55, 2, Some(2));
        match decide_fork(&[later, earlier.clone()]).expect("decision") {
            ForkDecision::Resolved(winner) => assert_eq!(winner, &earlier),
            other => panic!("expected resolved, got {other:?}"),
        }
    }

    #[test]
    fn node_id_breaks_a_full_tie() {
        // Equal digests and set versions but different lengths make a real divergence; the
        // node id is then the final tie-breaker. Equal tapes from two nodes are unanimous.
        let higher = head("node-z", "set-1", 55, 3, Some(3));
        let lower = head("node-a", "set-1", 55, 2, Some(2));
        match decide_fork(&[higher, lower.clone()]).expect("decision") {
            ForkDecision::Resolved(winner) => assert_eq!(winner, &lower),
            other => panic!("expected resolved, got {other:?}"),
        }
    }

    #[test]
    fn identical_choice_keys_with_divergent_tapes_are_quarantined() {
        let a = head("node-x", "set-1", 55, 2, Some(2));
        let b = head("node-x", "set-1", 55, 3, Some(3));
        assert_eq!(
            decide_fork(&[a.clone(), b.clone()]),
            Ok(ForkDecision::Quarantined(vec![a, b]))
        );
    }

    #[test]
    fn a_quarantined_node_cannot_commit() {
        let tape = tape_n_events(2);
        let certificate = certificate_for_prefix(&tape, 2, &["node-a", "node-b"], 2);
        let chosen = head("node-a", "set-1", certificate.digest(), 2, Some(2));
        let divergent = head("node-a", "set-1", certificate.digest(), 3, Some(3));
        let candidates = [chosen.clone(), divergent];
        let quarantined = decide_fork(&candidates).expect("decision");
        assert_eq!(
            commit_guarded(&certificate, &tape, &quarantined),
            Err(CommitError::Quarantined)
        );
        let agreed = [chosen];
        let unanimous = decide_fork(&agreed).expect("decision");
        assert!(commit_guarded(&certificate, &tape, &unanimous).is_ok());
    }

    #[test]
    fn a_certified_tape_commits_after_deterministic_fork_resolution() {
        let tape = tape_n_events(3);
        let certificate = certificate_for_prefix(&tape, 3, &["node-a", "node-b", "node-c"], 2);
        let chosen = head("node-b", "set-1", certificate.digest(), 3, Some(3));
        let rival = head(
            "node-z",
            "set-1",
            certificate.digest().wrapping_add(1),
            4,
            Some(4),
        );
        let candidates = [rival, chosen.clone()];
        let decision = decide_fork(&candidates).expect("decision");
        assert!(matches!(&decision, ForkDecision::Resolved(winner) if *winner == &chosen));
        let commitment = commit_guarded(&certificate, &tape, &decision).expect("commit");
        assert_eq!(commitment.height, 3);
        assert_eq!(commitment.digest, certificate.digest());
    }
}
