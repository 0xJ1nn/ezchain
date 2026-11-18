//! Pure length-prefixed framing for the node-facing RPC/WebSocket transport.
//!
//! This is the wire primitive beneath the typed records in [`crate::wire`]: one frame is a
//! fixed six-byte header followed by an opaque byte payload. It performs no I/O, opens no
//! socket, runs no async runtime and knows nothing about WebSocket handshakes or message
//! semantics. A future transport will own the connection and call [`encode`] / [`decode`]
//! on whole buffers.
//!
//! # Frame contract
//!
//! ```text
//! offset  size  field
//! 0       1     version, currently [`VERSION`] == 1
//! 1       1     kind, one of the declared [`Kind`] bytes
//! 2       4     payload length, unsigned 32-bit big-endian (network byte order)
//! 6       n     payload, exactly `length` bytes (n may be zero)
//! ```
//!
//! The total frame size is therefore `6 + length`. The maximum payload is a caller-supplied
//! [`Limits`] value, never a literal here (CONTRACT I7): a receiver refuses a declared length
//! above that bound before allocating, and a sender refuses to produce a frame it would
//! reject. Because the length field is a `u32`, the effective cap is
//! `min(limits.max_payload_bytes, u32::MAX)`.
//!
//! [`decode`] is deliberately **strict**: its input must be exactly one frame. Extra bytes —
//! even the start of a second valid frame — are a [`DecodeError::TrailingBytes`] refusal, so
//! a caller cannot silently drop data. A future connection layer that needs to split a stream
//! will frame one buffer at a time and pass each exact slice here.
//!
//! No Python original exists for this transport (ADR-0052 assigns node-facing networking to
//! Rust), so this written contract is the oracle and every case below records it.

/// Bytes in the fixed header: version (1) + kind (1) + payload length (4).
pub const HEADER_LEN: usize = 6;

/// The only frame format version this codec writes and accepts.
pub const VERSION: u8 = 1;

/// The declared frame kinds. The byte values are fixed wire constants, not indices into an
/// array, so adding a kind later cannot renumber an existing one. Any other byte is an
/// [`DecodeError::UnknownKind`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    /// A request expecting a response.
    Request,
    /// A response to a request.
    Response,
    /// A one-way message with no response.
    Notification,
}

impl Kind {
    pub const REQUEST_BYTE: u8 = 0x01;
    pub const RESPONSE_BYTE: u8 = 0x02;
    pub const NOTIFICATION_BYTE: u8 = 0x03;

    /// Parse a declared kind byte, or `None` for any undeclared value.
    pub fn from_byte(byte: u8) -> Option<Self> {
        match byte {
            Self::REQUEST_BYTE => Some(Self::Request),
            Self::RESPONSE_BYTE => Some(Self::Response),
            Self::NOTIFICATION_BYTE => Some(Self::Notification),
            _ => None,
        }
    }

    /// The fixed wire byte for this kind.
    pub fn to_byte(self) -> u8 {
        match self {
            Self::Request => Self::REQUEST_BYTE,
            Self::Response => Self::RESPONSE_BYTE,
            Self::Notification => Self::NOTIFICATION_BYTE,
        }
    }
}

/// Caller-supplied size policy. Kept caller-side so one framing codec can serve several
/// connections with different limits (CONTRACT I7).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Limits {
    /// Largest accepted payload in bytes. A frame may be at most `HEADER_LEN + this`.
    pub max_payload_bytes: usize,
}

/// One decoded frame. The version is not a field because [`VERSION`] is the only version
/// this codec accepts; [`encode`] always writes it and [`decode`] always checks it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Frame {
    pub kind: Kind,
    pub payload: Vec<u8>,
}

impl Frame {
    pub fn new(kind: Kind, payload: Vec<u8>) -> Self {
        Self { kind, payload }
    }
}

/// Why a frame could not be encoded. Checked before any bytes are produced.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EncodeError {
    /// The payload is larger than the effective cap `min(limit, u32::MAX)`.
    Oversize { length: usize, max: usize },
}

/// Why a buffer could not be decoded. The variants are listed in the order [`decode`] checks
/// them, so a buffer with several faults reports one reproducible reason.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DecodeError {
    /// Fewer than [`HEADER_LEN`] bytes were supplied; `got` is the total byte count.
    TruncatedHeader { got: usize },
    /// The version byte is not [`VERSION`].
    UnknownVersion { got: u8 },
    /// The kind byte is not a declared [`Kind`].
    UnknownKind { got: u8 },
    /// The declared length exceeds the caller's payload limit. Checked before the payload
    /// is read, so a lying header cannot make the decoder allocate a large buffer.
    Oversize { length: u32, max: usize },
    /// The header declared more payload bytes than the buffer carries. `declared` is the
    /// header value; `available` is the number of bytes after the header.
    TruncatedPayload { declared: u32, available: usize },
    /// The buffer carries more bytes than the header declares. `available` is the number of
    /// bytes after the header; the surplus is never ignored.
    TrailingBytes { declared: u32, available: usize },
}

/// Encode one frame into a fresh buffer: version, kind, big-endian `u32` length, payload.
///
/// Refuses an oversize payload rather than truncating its length into the header or emitting
/// a frame the peer would reject under the same limit.
pub fn encode(frame: &Frame, limits: Limits) -> Result<Vec<u8>, EncodeError> {
    let length = frame.payload.len();
    let max = limits.max_payload_bytes.min(u32::MAX as usize);
    if length > max {
        return Err(EncodeError::Oversize { length, max });
    }
    let mut out = Vec::with_capacity(HEADER_LEN + length);
    out.push(VERSION);
    out.push(frame.kind.to_byte());
    out.extend_from_slice(&(length as u32).to_be_bytes());
    out.extend_from_slice(&frame.payload);
    Ok(out)
}

/// Decode exactly one frame from `input`, or return the first typed [`DecodeError`].
///
/// Checks run in the order documented on [`DecodeError`]. On success every byte of `input`
/// belongs to the frame: there is no partial consumption and no leftover.
pub fn decode(input: &[u8], limits: Limits) -> Result<Frame, DecodeError> {
    if input.len() < HEADER_LEN {
        return Err(DecodeError::TruncatedHeader { got: input.len() });
    }
    let version = input[0];
    if version != VERSION {
        return Err(DecodeError::UnknownVersion { got: version });
    }
    let kind = Kind::from_byte(input[1]).ok_or(DecodeError::UnknownKind { got: input[1] })?;
    let length = u32::from_be_bytes([input[2], input[3], input[4], input[5]]);
    if u64::from(length) > limits.max_payload_bytes as u64 {
        return Err(DecodeError::Oversize {
            length,
            max: limits.max_payload_bytes,
        });
    }
    let available = input.len() - HEADER_LEN;
    if (available as u64) < u64::from(length) {
        return Err(DecodeError::TruncatedPayload {
            declared: length,
            available,
        });
    }
    if (available as u64) > u64::from(length) {
        return Err(DecodeError::TrailingBytes {
            declared: length,
            available,
        });
    }
    Ok(Frame {
        kind,
        payload: input[HEADER_LEN..].to_vec(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const LIMIT: Limits = Limits {
        max_payload_bytes: 1024,
    };

    fn header(kind_byte: u8, length: u32) -> Vec<u8> {
        let mut bytes = vec![VERSION, kind_byte];
        bytes.extend_from_slice(&length.to_be_bytes());
        bytes
    }

    fn framed(kind_byte: u8, payload: &[u8]) -> Vec<u8> {
        let mut bytes = header(kind_byte, payload.len() as u32);
        bytes.extend_from_slice(payload);
        bytes
    }

    #[test]
    fn header_layout_is_version_kind_and_big_endian_length() {
        assert_eq!(
            encode(&Frame::new(Kind::Request, vec![]), LIMIT),
            Ok(vec![0x01, 0x01, 0x00, 0x00, 0x00, 0x00])
        );
        assert_eq!(
            encode(
                &Frame::new(Kind::Response, vec![0xde, 0xad, 0xbe, 0xef]),
                LIMIT
            ),
            Ok(vec![
                0x01, 0x02, 0x00, 0x00, 0x00, 0x04, 0xde, 0xad, 0xbe, 0xef
            ])
        );
        assert_eq!(
            encode(&Frame::new(Kind::Notification, vec![0x07]), LIMIT),
            Ok(vec![0x01, 0x03, 0x00, 0x00, 0x00, 0x01, 0x07])
        );
        assert_eq!(HEADER_LEN, 6);
        assert_eq!(VERSION, 1);
    }

    #[test]
    fn kind_bytes_are_stable_and_closed() {
        for kind in [Kind::Request, Kind::Response, Kind::Notification] {
            assert_eq!(Kind::from_byte(kind.to_byte()), Some(kind));
        }
        assert_eq!(Kind::Request.to_byte(), 0x01);
        assert_eq!(Kind::Response.to_byte(), 0x02);
        assert_eq!(Kind::Notification.to_byte(), 0x03);
        let declared = [
            Kind::REQUEST_BYTE,
            Kind::RESPONSE_BYTE,
            Kind::NOTIFICATION_BYTE,
        ];
        for byte in 0..=u8::MAX {
            assert_eq!(
                Kind::from_byte(byte).is_some(),
                declared.contains(&byte),
                "declared state of byte {byte:#04x}"
            );
        }
    }

    #[test]
    fn empty_payload_round_trips_as_a_six_byte_frame() {
        for kind in [Kind::Request, Kind::Response, Kind::Notification] {
            let bytes = encode(&Frame::new(kind, vec![]), LIMIT).unwrap();
            assert_eq!(bytes.len(), HEADER_LEN);
            assert_eq!(decode(&bytes, LIMIT), Ok(Frame::new(kind, vec![])));
        }
    }

    #[test]
    fn round_trip_preserves_binary_payloads_at_every_boundary_size() {
        let kinds = [Kind::Request, Kind::Response, Kind::Notification];
        for size in [0, 1, 2, 5, 6, 7, 255, 256, 1023, 1024] {
            // Deterministic byte pattern spanning the full u8 range, not text.
            let payload: Vec<u8> = (0..size)
                .map(|i| (i as u8).wrapping_mul(31).wrapping_add(7))
                .collect();
            for &kind in &kinds {
                let bytes = encode(&Frame::new(kind, payload.clone()), LIMIT).unwrap();
                assert_eq!(bytes.len(), HEADER_LEN + size);
                assert_eq!(
                    decode(&bytes, LIMIT),
                    Ok(Frame::new(kind, payload.clone())),
                    "round trip of {size} bytes as {kind:?}"
                );
            }
        }
        let all_bytes: Vec<u8> = (0..=u8::MAX).collect();
        let bytes = encode(&Frame::new(Kind::Request, all_bytes.clone()), LIMIT).unwrap();
        assert_eq!(
            decode(&bytes, LIMIT),
            Ok(Frame::new(Kind::Request, all_bytes))
        );
    }

    #[test]
    fn payload_bytes_that_look_like_a_header_are_not_special() {
        // A payload may contain the version byte, a kind byte and a big-endian length. Only
        // the fixed six-byte prefix is a header; nothing inside the payload is parsed.
        let payload = vec![0x01, 0x02, 0x00, 0x00, 0x00, 0x02, 0xff, 0x00];
        let bytes = encode(&Frame::new(Kind::Notification, payload.clone()), LIMIT).unwrap();
        assert_eq!(bytes, framed(0x03, &payload));
        assert_eq!(
            decode(&bytes, LIMIT),
            Ok(Frame::new(Kind::Notification, payload))
        );
    }

    #[test]
    fn a_payload_at_the_limit_is_accepted_and_one_over_is_refused() {
        let tight = Limits {
            max_payload_bytes: 8,
        };
        let at_limit = vec![0xaa; 8];
        let bytes = encode(&Frame::new(Kind::Request, at_limit.clone()), tight).unwrap();
        assert_eq!(
            decode(&bytes, tight),
            Ok(Frame::new(Kind::Request, at_limit))
        );

        assert_eq!(
            encode(&Frame::new(Kind::Request, vec![0; 9]), tight),
            Err(EncodeError::Oversize { length: 9, max: 8 })
        );
        let mut over = header(Kind::REQUEST_BYTE, 9);
        over.extend_from_slice(&[0; 9]);
        assert_eq!(
            decode(&over, tight),
            Err(DecodeError::Oversize { length: 9, max: 8 })
        );
    }

    #[test]
    fn encode_never_writes_a_truncated_length() {
        // With the limit at u32::MAX the header must still hold the length exactly; a small
        // payload is written as four big-endian bytes, never as a narrower integer.
        let wide = Limits {
            max_payload_bytes: u32::MAX as usize,
        };
        let bytes = encode(&Frame::new(Kind::Response, vec![1, 2, 3]), wide).unwrap();
        assert_eq!(&bytes[2..6], &[0x00, 0x00, 0x00, 0x03]);
        assert_eq!(
            decode(&bytes, wide),
            Ok(Frame::new(Kind::Response, vec![1, 2, 3]))
        );
    }

    #[test]
    fn a_truncated_header_has_its_own_error() {
        for got in 0..HEADER_LEN {
            assert_eq!(
                decode(&[0x01, 0x01, 0x00, 0x00, 0x00][..got], LIMIT),
                Err(DecodeError::TruncatedHeader { got })
            );
        }
    }

    #[test]
    fn an_unknown_version_has_its_own_error() {
        for version in [0x00, 0x02, 0xff] {
            let mut bytes = header(Kind::REQUEST_BYTE, 0);
            bytes[0] = version;
            assert_eq!(
                decode(&bytes, LIMIT),
                Err(DecodeError::UnknownVersion { got: version })
            );
        }
    }

    #[test]
    fn an_unknown_kind_has_its_own_error() {
        for kind_byte in [0x00, 0x04, 0x7f, 0xff] {
            assert_eq!(
                decode(&header(kind_byte, 0), LIMIT),
                Err(DecodeError::UnknownKind { got: kind_byte })
            );
        }
    }

    #[test]
    fn an_oversize_declared_length_is_refused_before_the_payload_is_read() {
        // The buffer carries only a header. The length alone triggers the refusal; no
        // allocation of 4 GiB is attempted and no payload bytes are required.
        let length = u32::MAX;
        assert_eq!(
            decode(&header(Kind::Request.to_byte(), length), LIMIT),
            Err(DecodeError::Oversize {
                length,
                max: LIMIT.max_payload_bytes
            })
        );
    }

    #[test]
    fn a_truncated_payload_has_its_own_error() {
        assert_eq!(
            decode(
                &framed(Kind::REQUEST_BYTE, &[1, 2, 3, 4])[..HEADER_LEN + 3],
                LIMIT
            ),
            Err(DecodeError::TruncatedPayload {
                declared: 4,
                available: 3
            })
        );
        assert_eq!(
            decode(&header(Kind::NOTIFICATION_BYTE, 1), LIMIT),
            Err(DecodeError::TruncatedPayload {
                declared: 1,
                available: 0
            })
        );
    }

    #[test]
    fn trailing_bytes_have_their_own_error() {
        let mut bytes = encode(&Frame::new(Kind::Request, vec![0xab]), LIMIT).unwrap();
        bytes.push(0x00);
        assert_eq!(
            decode(&bytes, LIMIT),
            Err(DecodeError::TrailingBytes {
                declared: 1,
                available: 2
            })
        );

        // Even a second, fully valid frame in the same buffer is trailing data, not a
        // frame the decoder may silently consume and discard.
        let mut two = encode(&Frame::new(Kind::Request, vec![]), LIMIT).unwrap();
        two.extend(encode(&Frame::new(Kind::Response, vec![]), LIMIT).unwrap());
        assert_eq!(
            decode(&two, LIMIT),
            Err(DecodeError::TrailingBytes {
                declared: 0,
                available: HEADER_LEN
            })
        );
    }

    #[test]
    fn check_order_is_fixed_for_competing_faults() {
        // Too short to hold a header wins over the bad bytes it does contain.
        assert_eq!(
            decode(&[0xff, 0xff], LIMIT),
            Err(DecodeError::TruncatedHeader { got: 2 })
        );
        // A bad version wins over a bad kind and a bad length.
        let mut bytes = header(0xff, u32::MAX);
        bytes[0] = 0xff;
        assert_eq!(
            decode(&bytes, LIMIT),
            Err(DecodeError::UnknownVersion { got: 0xff })
        );
        // A bad kind wins over an oversize length.
        assert_eq!(
            decode(&header(0xff, u32::MAX), LIMIT),
            Err(DecodeError::UnknownKind { got: 0xff })
        );
        // An oversize length wins over a missing payload.
        assert_eq!(
            decode(&header(Kind::REQUEST_BYTE, u32::MAX), LIMIT),
            Err(DecodeError::Oversize {
                length: u32::MAX,
                max: LIMIT.max_payload_bytes
            })
        );
    }

    #[test]
    fn every_declared_kind_decodes_from_its_own_byte() {
        for (kind_byte, kind) in [
            (Kind::REQUEST_BYTE, Kind::Request),
            (Kind::RESPONSE_BYTE, Kind::Response),
            (Kind::NOTIFICATION_BYTE, Kind::Notification),
        ] {
            assert_eq!(
                decode(&framed(kind_byte, &[0x00, 0xff]), LIMIT),
                Ok(Frame::new(kind, vec![0x00, 0xff]))
            );
        }
    }
}
