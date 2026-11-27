//! Length-prefixed wire framing for peer envelopes.
//!
//! The frame body is the envelope's **existing canonical encoding** from
//! [`ezchain_p2p::envelope::canonical_bytes`] — length-prefixed fields in a fixed order —
//! followed by the claimed 64-bit digest as eight little-endian bytes. The canonical bytes
//! deliberately exclude the digest, so appending it is the minimal wire extension: the
//! receiver decodes all eight fields and passes the whole `Envelope` to `GossipKernel::handle`,
//! which recomputes the digest over the canonical fields and refuses a mismatch.
//!
//! On the TCP stream a frame is a 4-byte little-endian body length followed by exactly that
//! many body bytes. Every length is checked against the remaining buffer and the caller's
//! `max_frame_bytes` **before** any allocation, so malformed input returns a typed error and
//! never panics or allocates an unbounded buffer.

use std::{error::Error as StdError, fmt, io};

use ezchain_p2p::envelope::{canonical_bytes, Envelope};
use tokio::io::{AsyncRead, AsyncReadExt};

/// Number of bytes in the frame length prefix.
pub const PREFIX_LEN: usize = 4;

/// A typed reason a frame could not be framed or decoded. No variant carries an `Envelope`:
/// a parse failure closes only its connection (fail closed, CONTRACT I3).
#[derive(Debug)]
pub enum CodecError {
    /// An underlying transport read failed.
    Io(io::Error),
    /// A zero body length arrived on the wire.
    BadFrameLength { len: u32 },
    /// The frame body exceeded the caller-supplied bound (or the 4-byte prefix ceiling).
    FrameTooLarge { len: usize, max: usize },
    /// An inner length-prefixed field claimed more bytes than the frame contains.
    FieldTooLarge { len: u64, remaining: usize },
    /// A string field was not valid UTF-8.
    InvalidUtf8,
    /// The decoded body had bytes left over after the digest.
    TrailingBytes { remaining: usize },
}

impl fmt::Display for CodecError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Io(e) => write!(f, "frame read failed: {e}"),
            Self::BadFrameLength { len } => write!(f, "invalid frame length {len}"),
            Self::FrameTooLarge { len, max } => {
                write!(f, "frame body {len} bytes exceeds bound {max}")
            }
            Self::FieldTooLarge { len, remaining } => write!(
                f,
                "field length {len} exceeds remaining frame bytes {remaining}"
            ),
            Self::InvalidUtf8 => write!(f, "string field is not valid UTF-8"),
            Self::TrailingBytes { remaining } => {
                write!(f, "{remaining} trailing bytes after digest")
            }
        }
    }
}

impl StdError for CodecError {
    fn source(&self) -> Option<&(dyn StdError + 'static)> {
        match self {
            Self::Io(e) => Some(e),
            _ => None,
        }
    }
}

impl From<io::Error> for CodecError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}

/// Encode one envelope as a bounded length-prefixed frame.
///
/// Returns a typed [`CodecError::FrameTooLarge`] rather than truncating when the body would
/// exceed `max_frame_bytes`. `max_frame_bytes` is validated by [`crate::Node::bind`] to fit
/// the 4-byte prefix ceiling, so the cast here cannot truncate.
pub fn encode_frame(envelope: &Envelope, max_frame_bytes: usize) -> Result<Vec<u8>, CodecError> {
    let mut body = canonical_bytes(envelope);
    body.extend_from_slice(&envelope.digest.to_le_bytes());
    if body.len() > max_frame_bytes {
        return Err(CodecError::FrameTooLarge {
            len: body.len(),
            max: max_frame_bytes,
        });
    }
    let mut frame = Vec::with_capacity(PREFIX_LEN + body.len());
    frame.extend_from_slice(&(body.len() as u32).to_le_bytes());
    frame.extend_from_slice(&body);
    Ok(frame)
}

/// Read one bounded length-prefixed frame and decode it into an `Envelope`.
///
/// The complete raw frame (prefix and body) is returned alongside the envelope so an accepted
/// delivery can be forwarded verbatim to fanout peers with no re-encoding. Decoding only
/// parses; validation (digest, schema, membership, dedup, …) is the kernel's job.
pub async fn read_frame<R: AsyncRead + Unpin>(
    reader: &mut R,
    max_frame_bytes: usize,
) -> Result<(Envelope, Vec<u8>), CodecError> {
    let mut prefix = [0u8; PREFIX_LEN];
    reader.read_exact(&mut prefix).await?;
    let len = u32::from_le_bytes(prefix);
    if len == 0 {
        return Err(CodecError::BadFrameLength { len });
    }
    if (len as usize) > max_frame_bytes {
        return Err(CodecError::FrameTooLarge {
            len: len as usize,
            max: max_frame_bytes,
        });
    }
    let mut body = vec![0u8; len as usize];
    reader.read_exact(&mut body).await?;
    let envelope = decode_body(&body)?;
    let mut frame = Vec::with_capacity(PREFIX_LEN + body.len());
    frame.extend_from_slice(&prefix);
    frame.extend_from_slice(&body);
    Ok((envelope, frame))
}

/// A bounds-checking cursor over the body. Every read checks the remaining length first, so
/// no index can go out of bounds and no length can drive an unbounded allocation.
struct Cursor<'a> {
    bytes: &'a [u8],
    pos: usize,
}

impl<'a> Cursor<'a> {
    fn remaining(&self) -> usize {
        self.bytes.len() - self.pos
    }

    fn take(&mut self, count: usize) -> Result<&'a [u8], CodecError> {
        if count > self.remaining() {
            return Err(CodecError::FieldTooLarge {
                len: count as u64,
                remaining: self.remaining(),
            });
        }
        let slice = &self.bytes[self.pos..self.pos + count];
        self.pos += count;
        Ok(slice)
    }

    fn u64(&mut self) -> Result<u64, CodecError> {
        let bytes = self.take(8)?;
        let mut array = [0u8; 8];
        array.copy_from_slice(bytes);
        Ok(u64::from_le_bytes(array))
    }

    fn sized_bytes(&mut self) -> Result<Vec<u8>, CodecError> {
        let len = self.u64()?;
        if len > usize::MAX as u64 {
            return Err(CodecError::FieldTooLarge {
                len,
                remaining: self.remaining(),
            });
        }
        Ok(self.take(len as usize)?.to_vec())
    }

    fn string(&mut self) -> Result<String, CodecError> {
        let bytes = self.sized_bytes()?;
        String::from_utf8(bytes).map_err(|_| CodecError::InvalidUtf8)
    }
}

/// Decode a body in exactly the field order of `envelope::canonical_bytes`, plus the digest.
fn decode_body(body: &[u8]) -> Result<Envelope, CodecError> {
    let mut cursor = Cursor {
        bytes: body,
        pos: 0,
    };
    let schema = cursor.string()?;
    let kind = cursor.string()?;
    let sender = cursor.string()?;
    let set_version = cursor.string()?;
    let seq = cursor.u64()?;
    let payload = cursor.sized_bytes()?;
    let timestamp = cursor.u64()?;
    let digest = cursor.u64()?;
    if cursor.remaining() != 0 {
        return Err(CodecError::TrailingBytes {
            remaining: cursor.remaining(),
        });
    }
    Ok(Envelope {
        schema,
        kind,
        sender,
        set_version,
        seq,
        payload,
        timestamp,
        digest,
    })
}

#[cfg(test)]
mod tests {
    use super::{decode_body, encode_frame, read_frame, CodecError};
    use ezchain_p2p::envelope::{canonical_bytes, envelope_id, Envelope, SCHEMA};

    /// Number of bytes used to encode the trailing digest.
    const DIGEST_LEN: usize = 8;

    fn envelope() -> Envelope {
        let mut e = Envelope {
            schema: SCHEMA.to_string(),
            kind: "tape_event".to_string(),
            sender: "node-a".to_string(),
            set_version: "set-1".to_string(),
            seq: 1,
            payload: vec![1, 2, 3],
            timestamp: 1_700_000_000,
            digest: 0,
        };
        e.digest = envelope_id(&e);
        e
    }

    #[tokio::test]
    async fn a_frame_round_trips_to_the_same_envelope_and_raw_frame() {
        let frame = encode_frame(&envelope(), 1024).unwrap();
        let (decoded, raw_frame) = read_frame(&mut &frame[..], 1024).await.unwrap();
        assert_eq!(decoded, envelope());
        // The returned bytes include the prefix, so they can be forwarded verbatim.
        assert_eq!(raw_frame, frame);
        let mut expected_body = canonical_bytes(&envelope());
        expected_body.extend_from_slice(&envelope().digest.to_le_bytes());
        assert_eq!(&raw_frame[4..], expected_body.as_slice());
    }

    #[test]
    fn a_frame_larger_than_the_bound_is_refused() {
        let error = encode_frame(&envelope(), 8).unwrap_err();
        assert!(matches!(error, CodecError::FrameTooLarge { .. }));
    }

    #[tokio::test]
    async fn a_zero_length_prefix_is_refused() {
        let error = read_frame(&mut &0u32.to_le_bytes()[..], 1024)
            .await
            .unwrap_err();
        assert!(matches!(error, CodecError::BadFrameLength { len: 0 }));
    }

    #[tokio::test]
    async fn a_length_prefix_above_the_bound_is_refused() {
        let error = read_frame(&mut &2000u32.to_le_bytes()[..], 1024)
            .await
            .unwrap_err();
        assert!(matches!(
            error,
            CodecError::FrameTooLarge {
                len: 2000,
                max: 1024
            }
        ));
    }

    #[tokio::test]
    async fn a_truncated_body_is_refused() {
        let frame = encode_frame(&envelope(), 1024).unwrap();
        let truncated = &frame[..frame.len() - 3];
        let error = read_frame(&mut &truncated[..], 1024).await.unwrap_err();
        assert!(matches!(error, CodecError::Io(_)));
    }

    #[test]
    fn an_inner_field_length_beyond_the_frame_is_refused() {
        // First field is schema: claim 9 bytes but provide only four.
        let mut body = Vec::new();
        body.extend_from_slice(&9u64.to_le_bytes());
        body.extend_from_slice(b"abcd");
        body.extend_from_slice(&0u64.to_le_bytes());
        assert!(matches!(
            decode_body(&body),
            Err(CodecError::FieldTooLarge { .. })
        ));
    }

    #[test]
    fn an_invalid_utf8_string_field_is_refused() {
        let mut body = Vec::new();
        body.extend_from_slice(&1u64.to_le_bytes());
        body.extend_from_slice(&[0xff]); // invalid UTF-8 schema
        assert!(matches!(decode_body(&body), Err(CodecError::InvalidUtf8)));
    }

    #[test]
    fn trailing_bytes_after_the_digest_are_refused() {
        let frame = encode_frame(&envelope(), 1024).unwrap();
        let body = &frame[4..];
        let mut padded = body.to_vec();
        padded.push(0);
        assert!(matches!(
            decode_body(&padded),
            Err(CodecError::TrailingBytes { remaining: 1 })
        ));
    }

    #[test]
    fn the_digest_is_eight_bytes_on_the_wire() {
        let frame = encode_frame(&envelope(), 1024).unwrap();
        assert!(frame.len() >= 4 + DIGEST_LEN);
        let tail = &frame[frame.len() - DIGEST_LEN..];
        let mut bytes = [0u8; DIGEST_LEN];
        bytes.copy_from_slice(tail);
        assert_eq!(u64::from_le_bytes(bytes), envelope().digest);
    }
}
