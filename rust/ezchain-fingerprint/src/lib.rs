//! SHA-256 fingerprint over canonical transaction JSON bytes.
//!
//! # Digest contract
//!
//! This crate computes a digest only. It has no idea what a transaction is and
//! never re-serialises anything: callers hand it the **exact bytes** the Python
//! original would hash, and it returns the **exact lowercase hex digest** the
//! Python original returns.
//!
//! - **Input** (`&[u8]`): the canonical UTF-8 bytes produced by
//!   `ezchain-jsoncanon` — that is,
//!   `json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)`
//!   with the default `ensure_ascii=True` — or the exact raw bytes a signing
//!   boundary prepared. Accepting bytes instead of a JSON value keeps this
//!   crate decoupled from the canon writer and makes the contract explicit:
//!   the caller owns canonicalisation, this crate owns hashing.
//! - **Output**: `hashlib.sha256(raw).hexdigest()` — 64 lowercase ASCII hex
//!   characters, no `0x` prefix, no separator, no trailing newline. This is
//!   what `ApprovedIntent.digest` and `PreparedTransaction.payload_digest` in
//!   `packages/core-py/src/ezchain_core/signing.py` return for the same bytes.
//!
//! A digest is an identifier, not a signature. There is no signing here, no key
//! material, no vault and no I/O. Live mode stays owner-blocked (ADR-0026,
//! ADR-0035, ADR-0050 rule 3). Money arithmetic stays in Python until decimal
//! parity is proven (ADR-0049/0050). The well-audited pure-Rust `sha2` crate is
//! used because ADR-0052 assigns cryptographic operations to Rust.
//!
//! # Example
//!
//! ```
//! use ezchain_fingerprint::fingerprint;
//!
//! // Canonical bytes of {"a":1} (sorted keys, no whitespace).
//! let canonical = br#"{"a":1}"#;
//! assert_eq!(
//!     fingerprint(canonical),
//!     "015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862"
//! );
//! ```

#![forbid(unsafe_code)]

use sha2::{Digest, Sha256};

/// SHA-256 of `canonical` as lowercase hex, byte-identical to Python's
/// `hashlib.sha256(raw).hexdigest()` for the same bytes.
///
/// `canonical` must already be the canonical byte sequence (see the module
/// docs). No validation, normalisation, encoding or canonicalisation happens
/// here: two different byte sequences produce two different digests, which is
/// the point of the contract.
pub fn fingerprint(canonical: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let digest = Sha256::digest(canonical);
    let mut out = String::with_capacity(digest.len() * 2);
    for byte in digest {
        out.push(HEX[(byte >> 4) as usize] as char);
        out.push(HEX[(byte & 0x0f) as usize] as char);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn known_answer_empty() {
        assert_eq!(
            fingerprint(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
    }

    #[test]
    fn known_answer_short_inputs() {
        assert_eq!(
            fingerprint(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert_eq!(
            fingerprint(br#"{"a":1}"#),
            "015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862"
        );
    }

    #[test]
    fn known_answer_multiblock() {
        assert_eq!(
            fingerprint(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
            "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"
        );
    }

    #[test]
    fn output_is_lowercase_hex_of_length_64() {
        let out = fingerprint(b"ezchain");
        assert_eq!(out.len(), 64);
        assert!(out
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)));
    }

    #[test]
    fn different_bytes_produce_different_digests() {
        assert_ne!(fingerprint(br#"{"a":1}"#), fingerprint(br#"{"a":2}"#));
    }
}
