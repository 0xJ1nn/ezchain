//! Peer-message envelope validation, bounded sizes, typed rejections and a deterministic id.
//!
//! Oracle: ADR-0051 §§ "Message contract" and "Lifecycle" in `docs/DECISIONS.md`. The digest is a
//! deterministic, non-cryptographic content id (FNV-1a over canonical bytes); it is identity and
//! in-process tamper evidence within a trusted operator group, not a signature or a security
//! boundary. Authentication of node identifiers is a separate decision (ADR-0051).

/// The schema accepted by this kernel (ADR-0051 §"Message contract").
pub const SCHEMA: &str = "ezchain.p2p/1";

/// The declared message kinds. Only `tape_event` exists in this slice (ADR-0051).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    TapeEvent,
}

impl Kind {
    fn parse(text: &str) -> Option<Self> {
        match text {
            "tape_event" => Some(Kind::TapeEvent),
            _ => None,
        }
    }
}

/// Caller-supplied size bounds. No size policy is hardcoded here (ADR-0051; CONTRACT I7).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Limits {
    /// Maximum length of the canonical encoding of an accepted envelope.
    pub max_envelope_bytes: usize,
    /// Maximum accepted payload length in bytes.
    pub max_payload_bytes: usize,
}

/// A peer-message envelope as received from a caller. `digest` is the id the sender claims; the
/// validator recomputes it from the other fields and refuses a mismatch.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Envelope {
    pub schema: String,
    pub kind: String,
    pub sender: String,
    pub set_version: String,
    pub seq: u64,
    pub payload: Vec<u8>,
    pub timestamp: u64,
    pub digest: u64,
}

/// The typed reason an envelope was refused (ADR-0051 §"Message contract"). The variants are
/// ordered by the check order in [`validate`], which makes the reported reason reproducible.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Rejection {
    UnsupportedSchema,
    UnknownKind,
    SenderEmpty,
    SetVersionEmpty,
    SequenceZero,
    PayloadEmpty,
    PayloadTooLarge,
    EnvelopeTooLarge,
    DigestMismatch,
}

fn push_u64(out: &mut Vec<u8>, value: u64) {
    out.extend_from_slice(&value.to_le_bytes());
}

fn push_bytes(out: &mut Vec<u8>, bytes: &[u8]) {
    push_u64(out, bytes.len() as u64);
    out.extend_from_slice(bytes);
}

/// Canonical, length-prefixed bytes of everything the digest covers. Field order is fixed and no
/// map iteration is involved, so the encoding is identical for every caller. The `digest` field is
/// deliberately excluded: it is the value computed over these bytes.
pub fn canonical_bytes(envelope: &Envelope) -> Vec<u8> {
    let mut out = Vec::new();
    push_bytes(&mut out, envelope.schema.as_bytes());
    push_bytes(&mut out, envelope.kind.as_bytes());
    push_bytes(&mut out, envelope.sender.as_bytes());
    push_bytes(&mut out, envelope.set_version.as_bytes());
    push_u64(&mut out, envelope.seq);
    push_bytes(&mut out, &envelope.payload);
    push_u64(&mut out, envelope.timestamp);
    out
}

/// The deterministic content id of `bytes` (FNV-1a, 64-bit). Dependency-free by design; see the
/// module note for what this does and does not prove.
pub fn digest_bytes(bytes: &[u8]) -> u64 {
    const OFFSET: u64 = 0xcbf2_9ce4_8422_2325;
    const PRIME: u64 = 0x0000_0100_0000_01b3;
    let mut hash = OFFSET;
    for &byte in bytes {
        hash ^= u64::from(byte);
        hash = hash.wrapping_mul(PRIME);
    }
    hash
}

/// The deterministic id an envelope would carry if its fields are valid.
pub fn envelope_id(envelope: &Envelope) -> u64 {
    digest_bytes(&canonical_bytes(envelope))
}

/// Validate an envelope against the caller's limits. Returns its deterministic id on success and
/// a typed [`Rejection`] on failure. Checks run in the order documented on [`Rejection`]; a
/// refusal never mutates state and is never treated as an empty command (ADR-0051, CONTRACT I3).
pub fn validate(envelope: &Envelope, limits: Limits) -> Result<u64, Rejection> {
    if envelope.schema != SCHEMA {
        return Err(Rejection::UnsupportedSchema);
    }
    if Kind::parse(&envelope.kind).is_none() {
        return Err(Rejection::UnknownKind);
    }
    if envelope.sender.is_empty() {
        return Err(Rejection::SenderEmpty);
    }
    if envelope.set_version.is_empty() {
        return Err(Rejection::SetVersionEmpty);
    }
    if envelope.seq == 0 {
        return Err(Rejection::SequenceZero);
    }
    if envelope.payload.is_empty() {
        return Err(Rejection::PayloadEmpty);
    }
    if envelope.payload.len() > limits.max_payload_bytes {
        return Err(Rejection::PayloadTooLarge);
    }
    let canonical = canonical_bytes(envelope);
    if canonical.len() > limits.max_envelope_bytes {
        return Err(Rejection::EnvelopeTooLarge);
    }
    if digest_bytes(&canonical) != envelope.digest {
        return Err(Rejection::DigestMismatch);
    }
    Ok(digest_bytes(&canonical))
}

#[cfg(test)]
mod tests {
    use super::{
        canonical_bytes, digest_bytes, envelope_id, validate, Envelope, Limits, Rejection, SCHEMA,
    };

    // Oracle: ADR-0051 §"Message contract" (docs/DECISIONS.md). These cases are the written
    // contract itself; there is no Python original to record from.

    fn limits() -> Limits {
        Limits {
            max_envelope_bytes: 1024,
            max_payload_bytes: 256,
        }
    }

    fn sample() -> Envelope {
        let mut envelope = Envelope {
            schema: SCHEMA.to_string(),
            kind: "tape_event".to_string(),
            sender: "node-a".to_string(),
            set_version: "set-1".to_string(),
            seq: 1,
            payload: vec![1, 2, 3],
            timestamp: 1_700_000_000,
            digest: 0,
        };
        envelope.digest = envelope_id(&envelope);
        envelope
    }

    #[test]
    fn a_well_formed_envelope_is_accepted_with_its_deterministic_id() {
        let envelope = sample();
        assert_eq!(validate(&envelope, limits()), Ok(envelope.digest));
        assert_eq!(validate(&envelope, limits()), Ok(envelope_id(&envelope)));
    }

    #[test]
    fn every_refusal_has_its_typed_reason() {
        let mut e = sample();
        e.schema = "ezchain.p2p/0".to_string();
        assert_eq!(validate(&e, limits()), Err(Rejection::UnsupportedSchema));

        let mut e = sample();
        e.kind = "gossip".to_string();
        assert_eq!(validate(&e, limits()), Err(Rejection::UnknownKind));

        let mut e = sample();
        e.sender.clear();
        assert_eq!(validate(&e, limits()), Err(Rejection::SenderEmpty));

        let mut e = sample();
        e.set_version.clear();
        assert_eq!(validate(&e, limits()), Err(Rejection::SetVersionEmpty));

        let mut e = sample();
        e.seq = 0;
        assert_eq!(validate(&e, limits()), Err(Rejection::SequenceZero));

        let mut e = sample();
        e.payload.clear();
        assert_eq!(validate(&e, limits()), Err(Rejection::PayloadEmpty));

        let mut e = sample();
        e.payload = vec![0; 257];
        e.digest = envelope_id(&e);
        assert_eq!(validate(&e, limits()), Err(Rejection::PayloadTooLarge));

        let mut e = sample();
        e.payload = vec![0; 200];
        e.digest = envelope_id(&e);
        assert_eq!(
            validate(
                &e,
                Limits {
                    max_envelope_bytes: 64,
                    max_payload_bytes: 256,
                }
            ),
            Err(Rejection::EnvelopeTooLarge)
        );

        let mut e = sample();
        e.digest ^= 1;
        assert_eq!(validate(&e, limits()), Err(Rejection::DigestMismatch));
    }

    #[test]
    fn rejection_order_is_fixed_for_competing_faults() {
        let mut e = sample();
        e.schema = "wrong".to_string();
        e.digest = 0;
        assert_eq!(validate(&e, limits()), Err(Rejection::UnsupportedSchema));

        let mut e = sample();
        e.kind = "wrong".to_string();
        e.sender.clear();
        assert_eq!(validate(&e, limits()), Err(Rejection::UnknownKind));

        let mut e = sample();
        e.payload = vec![0; 257];
        e.digest = 0;
        assert_eq!(validate(&e, limits()), Err(Rejection::PayloadTooLarge));

        let mut e = sample();
        e.payload = vec![0; 200];
        e.digest = 0;
        assert_eq!(
            validate(
                &e,
                Limits {
                    max_envelope_bytes: 64,
                    max_payload_bytes: 256,
                }
            ),
            Err(Rejection::EnvelopeTooLarge)
        );
    }

    #[test]
    fn the_id_is_deterministic_and_changes_with_every_covered_field() {
        let base = sample();
        assert_eq!(envelope_id(&base), envelope_id(&sample()));

        let mut e = base.clone();
        e.schema.push('!');
        assert_ne!(envelope_id(&e), envelope_id(&base));

        let mut e = base.clone();
        e.kind.push('!');
        assert_ne!(envelope_id(&e), envelope_id(&base));

        let mut e = base.clone();
        e.sender.push('!');
        assert_ne!(envelope_id(&e), envelope_id(&base));

        let mut e = base.clone();
        e.set_version.push('!');
        assert_ne!(envelope_id(&e), envelope_id(&base));

        let mut e = base.clone();
        e.seq += 1;
        assert_ne!(envelope_id(&e), envelope_id(&base));

        let mut e = base.clone();
        e.payload.push(4);
        assert_ne!(envelope_id(&e), envelope_id(&base));

        let mut e = base.clone();
        e.timestamp += 1;
        assert_ne!(envelope_id(&e), envelope_id(&base));
    }

    #[test]
    fn length_prefixing_keeps_field_boundaries_distinct() {
        let mut a = sample();
        a.schema = "ab".to_string();
        a.kind = "c".to_string();

        let mut b = sample();
        b.schema = "a".to_string();
        b.kind = "bc".to_string();

        assert_ne!(canonical_bytes(&a), canonical_bytes(&b));
        assert_ne!(envelope_id(&a), envelope_id(&b));
    }

    #[test]
    fn the_claimed_digest_is_not_part_of_the_canonical_bytes() {
        let mut a = sample();
        let b = a.clone();
        a.digest = 0;
        assert_eq!(canonical_bytes(&a), canonical_bytes(&b));
        assert_eq!(digest_bytes(&canonical_bytes(&a)), envelope_id(&b));
    }
}
